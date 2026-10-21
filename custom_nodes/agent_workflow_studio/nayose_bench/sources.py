"""Real-world reference data for the contacts benchmark, downloaded once into a local cache (never
committed: some licenses require attribution or share-alike, and the files are large).

  Nemotron-Personas-Japan (NVIDIA, CC BY 4.0): names (taken from the persona text), sex, age and
      prefecture, distributed like Japan's 2024 census; names follow 名字由来net's distribution.
  JMnedict (EDRDG, Creative Commons Attribution-ShareAlike): name readings (one kanji, many readings).
  Japan Post postal code data ("著作権を主張しません。自由に配布していただいて結構です"): every
      prefecture, municipality and town with its kana.
  National Tax Agency corporate numbers (公共データ利用規約 第1.0版; 出典：国税庁法人番号公表サイト（国税庁）):
      real companies with furigana and head-office addresses.

python -m custom_nodes.agent_workflow_studio.nayose_bench.sources [--shards 2]
builds people.tsv, surnames.tsv, given.tsv, towns.tsv, companies.tsv in the cache.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import http.cookiejar
import io
import os
import random
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

CACHE = Path(
    os.environ.get("NAYOSE_SOURCES", "~/.cache/agent-workflow-studio/nayose_sources")
).expanduser()
NEMOTRON = "https://huggingface.co/datasets/nvidia/Nemotron-Personas-Japan/resolve/main/data/train-{:05d}-of-00008.parquet"
JMNEDICT = "http://ftp.edrdg.org/pub/Nihongo/JMnedict.xml.gz"
JAPAN_POST = "https://www.post.japanpost.jp/service/search/zipcode/download/utf/zip/utf_ken_all.zip"
NTA_PAGE = "https://www.houjin-bangou.nta.go.jp/download/zenken/"
NTA_DIFF = "https://www.houjin-bangou.nta.go.jp/download/sabun/"
ATTRIBUTION = """Sources (see each license):
- Nemotron-Personas-Japan, NVIDIA, CC BY 4.0. https://huggingface.co/datasets/nvidia/Nemotron-Personas-Japan
- JMnedict, Electronic Dictionary Research and Development Group, used in conformance with the Group's licence. https://www.edrdg.org/
- 郵便番号データ, 日本郵便株式会社 (no copyright claimed). https://www.post.japanpost.jp/zipcode/download.html
- 出典：国税庁法人番号公表サイト（国税庁）（https://www.houjin-bangou.nta.go.jp/）を加工して作成
"""
NAME = re.compile(r"^([^\s、。は]{1,6}) ([^\s、。]{1,8}?)(?:は|が|、)")
LEGAL = {
    "301": "株式会社",
    "302": "有限会社",
    "303": "合名会社",
    "304": "合資会社",
    "305": "合同会社",
}
LEGAL_STRIP = re.compile(r"株式会社|有限会社|合同会社|合名会社|合資会社|\s|　")
GROUP_SUFFIX = re.compile(
    r"(ホールディングス|ＨＤ|HD|グループ|販売|物流|サービス|システムズ|システム|エンジニアリング|不動産|開発|"
    r"商事|工業|製作所|技研|リース|ファイナンス|保険|建設|管理)$"
)


def fetch(url: str, path: Path, data: bytes | None = None, opener=None) -> Path:
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    request = urllib.request.Request(
        url, data=data, headers={"User-Agent": "agent-workflow-studio"}
    )
    try:
        with (
            (opener or urllib.request.build_opener()).open(request, timeout=600) as r,
            open(part, "wb") as f,
        ):
            while chunk := r.read(1 << 20):
                f.write(chunk)
    except urllib.error.URLError as error:
        if "CERTIFICATE" not in str(error) or data is not None:
            raise
        # This Python cannot verify the server's chain; the system curl uses the OS trust store.
        subprocess.run(["curl", "-sSfL", "-o", str(part), url], check=True)
    part.rename(path)
    return path


def people(shards: int) -> int:
    """Name, sex, age, prefecture from the persona text (「野本 花代子は…」)."""
    import pyarrow.parquet as pq

    out = CACHE / "people.tsv"
    if out.exists():
        return sum(1 for _ in open(out, encoding="utf-8")) - 1
    rows = 0
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["surname", "given", "sex", "age", "prefecture"])
        for i in range(shards):
            table = pq.read_table(
                fetch(NEMOTRON.format(i), CACHE / f"nemotron-{i}.parquet"),
                columns=["persona", "sex", "age", "prefecture"],
            )
            for persona, sex, age, pref in zip(
                *(table[c].to_pylist() for c in table.column_names), strict=True
            ):
                if m := NAME.match(persona or ""):
                    w.writerow([m.group(1), m.group(2), "M" if sex == "男" else "F", age, pref])
                    rows += 1
    return rows


def readings() -> tuple[dict, dict]:
    """JMnedict: kanji -> readings (katakana), for surnames and for given names (by sex)."""
    import xml.etree.ElementTree as ET

    path = fetch(JMNEDICT, CACHE / "JMnedict.xml.gz")
    sur, giv = defaultdict(list), defaultdict(list)

    def kind(text: str) -> str:  # the DTD expands &surname; etc. to their descriptions
        t = text.lower()
        return (
            "surname"
            if "surname" in t
            else "fem"
            if "female" in t
            else "masc"
            if "male" in t
            else "given"
            if "given" in t
            else ""
        )

    with gzip.open(path) as f:
        for _, e in ET.iterparse(f, events=("end",)):
            if e.tag != "entry":
                continue
            kanji = [k.text for k in e.iter("keb")]
            reads = [
                "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in r.text)
                for r in e.iter("reb")
            ]
            types = {kind(t.text or "") for t in e.iter("name_type")}
            for k in kanji:
                if "surname" in types:
                    sur[k] += [r for r in reads if r not in sur[k]]
                for t in ("fem", "masc", "given"):
                    if t in types:
                        giv[(k, t)] += [r for r in reads if r not in giv[(k, t)]]
            e.clear()
    return sur, giv


def unvoiced(kana: str) -> str:
    import unicodedata

    return "".join(unicodedata.normalize("NFD", c)[0] for c in kana)


def reader():
    """(main reading, what a clerk guesses) of a name. MeCab with UniDic reads a name it knows as one
    proper noun (else the dictionary's only reading). The guess is how real misreadings go: for a
    surname another listed reading differing only in voicing (ナカジマ/ナカシマ) or the unvoiced form;
    for a given name another listed reading of the same name (七星: ナナエ/ナナセ)."""
    import fugashi

    tagger = fugashi.Tagger()

    def read(name, listed, surname):
        tokens = list(tagger(name))
        if len(tokens) == 1 and tokens[0].feature.pos2 == "固有名詞" and tokens[0].feature.kana:
            main = tokens[0].feature.kana
        elif len(listed) == 1:
            main = listed[0]
        else:
            return None
        others = [r for r in listed if r != main]
        if surname:
            close = [r for r in others if unvoiced(r) == unvoiced(main)]
            guess = close[0] if close else unvoiced(main)
        else:
            guess = others[0] if others else main
        return main, guess

    return read


def name_tables() -> None:
    """surnames.tsv / given.tsv: real frequencies (Nemotron) with a main reading and a clerk's guess;
    names without a confident reading are left out."""
    if (CACHE / "given.tsv").exists():
        return
    sur_r, giv_r = readings()
    read = reader()
    sur, giv = Counter(), Counter()
    with open(CACHE / "people.tsv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            sur[r["surname"]] += 1
            giv[(r["given"], r["sex"])] += 1
    with open(CACHE / "surnames.tsv", "w", encoding="utf-8") as f:
        f.write("kanji\tcount\treadings\n")
        for k, c in sur.most_common():
            if r := read(k, sur_r.get(k, []), True):
                f.write(f"{k}\t{c}\t{r[0]}|{r[1]}\n")
    with open(CACHE / "given.tsv", "w", encoding="utf-8") as f:
        f.write("kanji\tsex\tcount\treadings\n")
        for (k, sex), c in giv.most_common():
            listed = (
                giv_r.get((k, "masc" if sex == "M" else "fem")) or giv_r.get((k, "given")) or []
            )
            if r := read(k, listed, False):
                f.write(f"{k}\t{sex}\t{c}\t{r[0]}|{r[1]}\n")


def towns() -> int:
    """towns.tsv: every town with its kana (Japan Post), whether it has chome."""
    out = CACHE / "towns.tsv"
    if out.exists():
        return sum(1 for _ in open(out, encoding="utf-8")) - 1
    with zipfile.ZipFile(fetch(JAPAN_POST, CACHE / "utf_ken_all.zip")) as z:
        text = z.read(z.namelist()[0]).decode("utf-8")
    seen, n = set(), 0
    with open(out, "w", encoding="utf-8") as f:
        f.write("zip\tpref\tcity\ttown\tpref_kana\tcity_kana\ttown_kana\tchome\n")
        for r in csv.reader(io.StringIO(text)):
            town = r[8]
            if "以下に掲載がない場合" in town or "（" in town or "、" in town or not town:
                continue  # catch-all and composite rows are not addresses
            key = (r[6], r[7], town)
            if key in seen:
                continue
            seen.add(key)
            f.write("\t".join([r[2], r[6], r[7], town, r[3], r[4], r[5], r[11]]) + "\n")
            n += 1
    return n


def companies(sample: int = 300_000, seed: int = 7) -> int:
    """companies.tsv: active companies with furigana (NTA), name, kind, head-office address."""
    out = CACHE / "companies.tsv"
    if out.exists():
        return sum(1 for _ in open(out, encoding="utf-8")) - 1
    path = CACHE / "nta-zenkoku-unicode.zip"
    if not path.exists():
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        page = (
            opener.open(
                urllib.request.Request(NTA_PAGE, headers={"User-Agent": "agent-workflow-studio"})
            )
            .read()
            .decode("utf-8")
        )
        token_name, token = re.search(r'name="(jp\.go[^"]+token)"\s+value="([^"]+)"', page).groups()
        section = page[page.find('id="csv-unicode"') : page.find('id="xml-unicode"')]
        number = re.search(r'<dt class="mb05">全国</dt>[\s\S]*?doDownload\((\d+)\)', section).group(
            1
        )
        body = urllib.parse.urlencode(
            {token_name: token, "event": "download", "selDlFileNo": number}
        ).encode()
        fetch(NTA_PAGE + "index.html", path, body, opener)
    rng = random.Random(seed)
    kept, seen = [], 0
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        with z.open(name) as raw:
            for r in csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                # 2 法人番号, 7 商号, 9 法人種別, 10-12 所在地, 16 郵便番号, 20 閉鎖事由, 29 フリガナ (1-based)
                if len(r) < 29 or r[19] or not r[28] or r[8] not in LEGAL and r[8] != "399":
                    continue
                seen += 1
                row = [
                    r[1],
                    r[6],
                    r[28],
                    LEGAL.get(r[8], "その他"),
                    r[9],
                    r[10],
                    r[11],
                    r[15],
                    r[24],
                ]
                if len(kept) < sample:
                    kept.append(row)
                elif (j := rng.randrange(seen)) < sample:
                    kept[j] = row
    with open(out, "w", encoding="utf-8") as f:
        f.write("number\tname\tkana\tkind\tpref\tcity\tstreet\tzip\tenglish\n")
        for row in kept:
            f.write("\t".join(x.replace("\t", " ") for x in row) + "\n")
    return len(kept)


def nta_form(page_url: str):
    """(opener with the session cookie, the form token field, token, page html)."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(page_url, headers={"User-Agent": "agent-workflow-studio"})
    page = opener.open(request).read().decode("utf-8")
    token_name, token = re.search(r'name="(jp\.go[^"]+token)"\s+value="([^"]+)"', page).groups()
    return opener, token_name, token, page


def nta_rows(path: Path):
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith(".csv"):
                with z.open(name) as raw:
                    yield from csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"))


def address_of(r: list) -> list:
    return [r[9], r[10], r[11]]


def company_events(per_kind: int = 20_000, seed: int = 7) -> dict:
    """Real company life events and look-alikes from the registry: renames and head-office moves
    (the daily update files against the full file), mergers (closed by merger, with the successor),
    same-name companies, and sibling companies at one address sharing a name stem."""
    if (CACHE / "groups.tsv").exists():
        return {}
    rng = random.Random(seed)
    # Daily updates (the last 40 days): which companies changed name or head office.
    opener, token_name, token, page = nta_form(NTA_DIFF)
    section = page[page.find('id="csv-unicode"') : page.find('id="xml-unicode"')]
    changed = {}
    for number in re.findall(r"doDownload\((\d+)\)", section):
        path = CACHE / "diff" / f"{number}.zip"
        form = {token_name: token, "event": "download", "selDlFileNo": number}
        fetch(NTA_DIFF + "index.html", path, urllib.parse.urlencode(form).encode(), opener)
        for r in nta_rows(path):
            if len(r) >= 29 and r[2] in ("11", "12"):  # 商号又は名称の変更 / 国内所在地の変更
                changed[r[1]] = (r[2], r[6], r[28], address_of(r), r[5])
    # Pass 1 over the full file: same names; mergers; the rows of companies changed since.
    zenken = CACHE / "nta-zenkoku-unicode.zip"
    names, mergers, before = defaultdict(list), [], {}
    for r in nta_rows(zenken):
        if len(r) < 29:
            continue
        if r[1] in changed:
            before[r[1]] = r
        if r[19] == "11" and r[20] and r[28]:  # closed by a merger, with its successor
            mergers.append(r)
        elif not r[19] and r[28] and r[8] in LEGAL and len(names[r[6]]) < 4:
            names[r[6]].append(r)
    same = [rows for rows in names.values() if len(rows) >= 2]
    rng.shuffle(same)
    rng.shuffle(mergers)
    mergers = mergers[:per_kind]
    successors = {r[20] for r in mergers}
    # Pass 2: the successors' rows, and every company at the address of a sampled company.
    sample = [rows[0] for rows in rng.sample(list(names.values()), min(per_kind * 5, len(names)))]
    at = {tuple(address_of(r)) for r in sample}
    succ, by_address = {}, defaultdict(list)
    for r in nta_rows(zenken):
        if len(r) < 29:
            continue
        if r[1] in successors:
            succ[r[1]] = r
        if not r[19] and r[28] and tuple(address_of(r)) in at:
            by_address[tuple(address_of(r))].append(r)
    groups = []
    for rows in by_address.values():
        cores = [LEGAL_STRIP.sub("", r[6]) for r in rows]
        stems = Counter(c[:2] for c in cores if len(c) >= 3)
        family = [r for r, c in zip(rows, cores, strict=True) if len(c) >= 3 and stems[c[:2]] >= 2]
        if 2 <= len(family) <= 6 and len({r[6] for r in family}) == len(family):
            groups.append(family)

    def row(r):
        return "\t".join([r[1], r[6], r[28], LEGAL.get(r[8], "その他"), *address_of(r)])

    def write(name, header, lines):
        with open(CACHE / name, "w", encoding="utf-8") as f:
            f.write(header + "\n" + "".join(line + "\n" for line in lines))
        return len(lines)

    cols = "number\tname\tkana\tkind\tpref\tcity\tstreet"
    renames = [
        "\t".join([n, before[n][6], c[1], c[2], LEGAL.get(before[n][8], "その他"), *c[3], c[4]])
        for n, c in changed.items()
        if c[0] == "11" and n in before and before[n][6] != c[1] and c[2]
    ]
    moves = [
        "\t".join(
            [n, c[1], c[2], LEGAL.get(before[n][8], "その他"), *address_of(before[n]), *c[3], c[4]]
        )
        for n, c in changed.items()
        if c[0] == "12" and n in before and address_of(before[n]) != c[3] and c[2]
    ]
    merged = [
        row(r) + f"\t{r[18]}\t" + row(succ[r[20]])
        for r in mergers
        if succ.get(r[20], [""] * 29)[28]
    ]
    return {
        "renames": write(
            "renames.tsv",
            "number\told_name\tnew_name\tnew_kana\tkind\tpref\tcity\tstreet\tdate",
            renames,
        ),
        "moves": write(
            "moves.tsv",
            "number\tname\tkana\tkind\told_pref\told_city\told_street\tpref\tcity\tstreet\tdate",
            moves,
        ),
        "mergers": write(
            "mergers.tsv",
            cols
            + "\tclosed\tsuccessor_number\tsuccessor_name\tsuccessor_kana\tsuccessor_kind\tsuccessor_pref\tsuccessor_city\tsuccessor_street",
            merged,
        ),
        "same_name": write(
            "same_name.tsv",
            "group\t" + cols,
            [f"{g}\t{row(r)}" for g, rows in enumerate(same[:per_kind]) for r in rows],
        ),
        "groups": write(
            "groups.tsv",
            "group\t" + cols,
            [f"{g}\t{row(r)}" for g, rows in enumerate(groups[:per_kind]) for r in rows],
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--shards", type=int, default=2, help="Nemotron shards (125k personas each)"
    )
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "ATTRIBUTION.txt").write_text(ATTRIBUTION, encoding="utf-8")
    print("people", people(args.shards))
    name_tables()
    print(
        "surnames",
        sum(1 for _ in open(CACHE / "surnames.tsv", encoding="utf-8")) - 1,
        "given",
        sum(1 for _ in open(CACHE / "given.tsv", encoding="utf-8")) - 1,
    )
    print("towns", towns())
    print("companies", companies())
    print("company events", company_events())
    print("cache", CACHE)


if __name__ == "__main__":
    main()
