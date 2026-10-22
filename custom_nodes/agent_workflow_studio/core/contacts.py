"""Contacts: read the exports through a column map, then standardize them into one record per key.

The measured pipeline (research/contacts_er, RESULTS.md): kanji variants folded, kana keys
(small -> large, ヅ/ヂ, long vowels), kana OCR repaired, phones canonical, addresses parsed against a
town registry, readings learned from the data, whole names split, shop names as a surname alias,
company names reduced to a folded core.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .context import Context

# --- the column map -------------------------------------------------------------------------------
COLUMNS = """# Which column holds what. Files are in the data folder. Only key, birth_date and one name
# column are required; leave out what your data does not have.
key: CONT_ID                      # or a list of columns that together identify a contact
contacts: contacts.csv
surname: {kanji: LAST_NAME_KANJI, kana: LAST_NAME_KANA, romaji: LAST_NAME_ROMAJI}
given: {kanji: FIRST_NAME_KANJI, kana: FIRST_NAME_KANA, romaji: FIRST_NAME_ROMAJI}
birth_date: BIRTH_DATE            # or {column: 生年月日, format: "%Y/%m/%d"}
gender: GENDER
type: {column: CONT_TYPE, company: ["2"]}   # the values that mean a company
phones: {file: phones.csv, columns: [PHONE]}          # no file: columns of the contacts file
addresses: {file: addresses.csv, kanji: [ADDRESS_KANJI]}  # several columns: parts of one address
"""
RAW_FIELDS = ("sur_kanji", "giv_kanji", "sur_kana", "giv_kana", "sur_romaji", "giv_romaji")


def column_map(text: str) -> dict:
    try:
        spec = yaml.safe_load(text or COLUMNS) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"The column map is not valid YAML: {error}") from None
    if not isinstance(spec, dict) or not spec.get("key") or not spec.get("birth_date"):
        raise ValueError("The column map needs at least `key` and `birth_date`.")
    names = [
        spec.get(part, {}).get(script)
        for part in ("surname", "given")
        for script in ("kanji", "kana")
        if isinstance(spec.get(part), dict)
    ]
    if not any(names):
        raise ValueError("The column map needs at least one name column (surname or given).")
    return spec


def _cols(value) -> list[str]:
    return [value] if isinstance(value, str) else [c for c in value or [] if c]


def _read(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _need(header: list[str], columns: list[str], file: str, what: str) -> None:
    missing = [c for c in columns if c not in header]
    if missing:
        raise ValueError(
            f"Column {', '.join(missing)} ({what}) is not in {file}; it has "
            f"{', '.join(header[:30])}. Map {what} in the column map (or remove it there if your "
            "data has none)."
        )


def read_contacts(ctx: Context, folder: str, columns: str) -> tuple[list[dict], dict]:
    """One raw contact per key, its phones and addresses attached, in the contacts file's order.
    Returns the contacts and what the shape check found."""
    spec = column_map(columns)
    file = spec.get("contacts") or "contacts.csv"
    header, rows = _read(ctx.data_path(folder, file))
    keys = _cols(spec["key"])
    _need(header, keys, file, "key")
    date = (
        spec["birth_date"]
        if isinstance(spec["birth_date"], dict)
        else {"column": spec["birth_date"]}
    )
    _need(header, [date["column"]], file, "birth_date")
    fields = {}
    for part, short in (("surname", "sur"), ("given", "giv")):
        for script in ("kanji", "kana", "romaji"):
            column = (spec.get(part) or {}).get(script)
            if column:
                _need(header, [column], file, f"{part} {script}")
                fields[f"{short}_{script}"] = column
    gender = spec.get("gender")
    gender = gender if isinstance(gender, dict) else {"column": gender} if gender else {}
    kind = spec.get("type")
    kind = kind if isinstance(kind, dict) else {"column": kind} if kind else {}
    for what, d in (("gender", gender), ("type", kind)):
        if d.get("column"):
            _need(header, [d["column"]], file, what)
    codes = {v: g for g, values in (gender.get("codes") or {}).items() for v in _cols(values)}
    company = set(_cols(kind.get("company")))
    person = set(_cols(kind.get("person")))

    def key_of(r: dict) -> str:
        return "|".join((r.get(k) or "").strip() for k in keys)

    records, stats = {}, Counter()
    for r in rows:
        key = key_of(r)
        if not key.strip("|"):
            stats["rows without a key"] += 1
            continue
        if key in records:
            stats["duplicate keys"] += 1
        raw_date = (r.get(date["column"]) or "").strip()
        if raw_date and date.get("format"):
            try:
                raw_date = datetime.strptime(raw_date, date["format"]).date().isoformat()
            except ValueError:
                stats["birth dates unreadable"] += 1
                raw_date = ""
        elif raw_date and not re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", raw_date):
            stats["birth dates unreadable"] += 1
        g = (r.get(gender.get("column") or "") or "").strip()
        flag = (r.get(kind.get("column") or "") or "").strip()
        if company or person:  # recorded values mapped: 2 = company, 1 = person
            flag = (
                "2" if flag in company else "1" if flag and (not person or flag in person) else ""
            )
        records[key] = {
            "key": key,
            **{f: r.get(c) or "" for f, c in fields.items()},
            "birth_date": raw_date,
            "gender": codes.get(g, g),
            "flag": flag,
            "phones": [],
            "addresses": [],
        }
    for spec_phone in _specs(spec.get("phones")):
        attach(ctx, folder, file, header, rows, spec_phone, keys, records, "phones", stats)
    for spec_addr in _specs(spec.get("addresses")):
        attach(ctx, folder, file, header, rows, spec_addr, keys, records, "addresses", stats)
    stats["contacts"] = len(records)
    return list(records.values()), dict(stats)


def _specs(value) -> list[dict]:
    if not value:
        return []
    return [value] if isinstance(value, dict) else [v for v in value if isinstance(v, dict)]


def attach(ctx, folder, file, header, rows, spec, keys, by_key, what, stats) -> None:
    """Phones or addresses from their own file (joined on the key) or from the contacts file."""
    if spec.get("file"):
        file = spec["file"]
        header, rows = _read(ctx.data_path(folder, file))
        _need(header, keys, file, "key")
    columns = _cols(spec.get("columns") if what == "phones" else spec.get("kanji"))
    _need(header, columns, file, what)
    for r in rows:
        rec = by_key.get("|".join((r.get(k) or "").strip() for k in keys))
        if rec is None:
            stats[f"{what} without a contact"] += 1
            continue
        if what == "phones":  # an empty cell is no phone
            rec["phones"] += [r[c] for c in columns if (r.get(c) or "").strip()]
        elif text := "".join((r.get(c) or "") for c in columns):  # parts of one address
            rec["addresses"].append(text)


def save_jsonl(path: Path, rows) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --- standardization (research/contacts_er/prep.py, level "full") ---------------------------------
# Public knowledge a real system takes from a registry (異体字 tables): kanji variants.
ITAIJI = dict(
    pair.split(":")
    for pair in "髙:高 﨑:崎 邊:辺 邉:辺 齋:斎 齊:斉 𠮷:吉 德:徳 澤:沢 濱:浜 嶋:島 櫻:桜 廣:広 國:国 龍:竜 惠:恵 冨:富".split()
)
PREFECTURES = (
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 "
    "富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 "
    "鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 "
    "鹿児島県 沖縄県"
).split()
SMALL = str.maketrans("ァィゥェォッャュョヮヵヶ", "アイウエオツヤユヨワカケ")
VOWEL = {}
for _row, _v in zip(
    [
        "アカサタナハマヤラワガザダバパャァ",
        "イキシチニヒミリギジヂビピィ",
        "ウクスツヌフムユルグズヅブプュゥヴ",
        "エケセテネヘメレゲゼデベペェ",
        "オコソトノホモヨロヲゴゾドボポョォ",
    ],
    "aiueo",
    strict=True,
):
    VOWEL |= dict.fromkeys(_row, _v)
KANSUJI = dict(zip("〇一二三四五六七八九", "0123456789", strict=True))
FIXES = ("kana_ocr", "readings", "split_one_field", "shop_alias", "company_clean")
RECIPIENT = re.compile(r"\s*御中\s*|\s+(\S*(部|課|室)(\s*\S+様)?|ご担当者様|担当\s*\S*|\S+様)\s*$")
DEPARTMENT = re.compile(r"^(\S*(部|課|室)|担当.*|代表|ご担当者.*)$")
LOANWORD = str.maketrans(
    {
        "ー": None,
        "・": None,
        "ヴ": "ブ",
        **dict(zip("ャュョァィゥェォッ", "ヤユヨアイウエオツ", strict=True)),
    }
)
KANA_OCR = str.maketrans("八卜口二工力夕一", "ハトロニエカター")
LEGAL_FORMS = re.compile(
    r"株式会社|有限会社|合同会社|合名会社|合資会社|一般社団法人|一般財団法人|医療法人|社会福祉法人|"
    r"[（(]\s*[株有同名資]\s*[）)]|㈱|㈲|カブシキガイシャ|ユウゲンガイシャ|ゴウドウガイシャ|"
    r"(?:^|\s)[カユドメシ]\)|\([カユドメシ](?:\s|$)"
)
BRANCH = re.compile(r"\s*\S{0,6}?(本社|本店|支店|営業所|出張所|工場|事業所)$")
SHOP_WORDS = re.compile(
    r"商店|工務店|美容室|理容店|歯科医院|クリニック|事務所|食堂|酒店|電器|農園|不動産|塗装|鍼灸院|教室"
)


def kansuji_number(s: str) -> str:
    """二十七 -> 27, 十 -> 10, 一〇五 -> 105 (block numbers written in kanji)."""
    if "十" not in s:
        return "".join(KANSUJI[c] for c in s)
    tens, _, ones = s.partition("十")
    if len(tens) > 1 or len(ones) > 1 or "十" in ones:  # dirt (OCR, typos): digit by digit
        return "".join(KANSUJI.get(c, "") for c in s)
    return str(int(KANSUJI[tens] if tens else 1) * 10 + (int(KANSUJI[ones]) if ones else 0))


def kata(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def is_kana(s: str) -> bool:
    return bool(s) and all("ァ" <= c <= "ヺ" or c in "ー・ " for c in s)


def fold_long(s: str) -> str:
    """Long vowels folded: オウ/オオ/オー -> オ, ウウ/ウー -> ウ, エイ/エー -> エ, any ー dropped."""
    out = []
    for c in s:
        prev = VOWEL.get(out[-1]) if out else None
        if c == "ー" or (prev == "o" and c in "ウオ") or (prev == "u" and c == "ウ"):
            continue
        if prev == "e" and c == "イ":
            continue
        out.append(c)
    return "".join(out)


def kana_key(s: str) -> str:
    s = kata(s).replace(" ", "").replace("・", "").replace("　", "")
    s = s.translate(SMALL).replace("ヅ", "ズ").replace("ヂ", "ジ")
    return fold_long(s)


def seion(s: str) -> str:
    """Without dakuten/handakuten: a clerk's unvoiced guess (ナカツカ for ナカヅカ) meets."""
    return "".join(unicodedata.normalize("NFD", c)[0] for c in s)


def kanji_key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace(" ", "").replace("　", "")
    return "".join(ITAIJI.get(c, c) for c in s)


def phone_key(s: str) -> str:
    digits = "".join(c for c in unicodedata.normalize("NFKC", s or "") if c.isdigit())
    if digits.startswith("81") and len(digits) in (11, 12):
        digits = "0" + digits[2:]
    if digits and digits[0] != "0" and len(digits) in (9, 10):
        digits = "0" + digits  # a lost leading 0
    if not digits or set(digits) <= {"0"} or len(digits) not in (10, 11):
        return ""
    return digits


def fold_company(core: str) -> str:
    """A company core with loanword spellings folded: コンピューター/コンピュータ, ヴ/ブ, ティ/チ,
    キャノン/キヤノン, ・, ＆/アンド."""
    core = (
        core.replace("ティ", "チ").replace("ディ", "ジ").replace("アンド", "&").replace("＆", "&")
    )
    return core.translate(LOANWORD)


def company_core(text: str) -> str:
    """A company name without its legal form, branch and spacing: 株式会社サンプル 東京支店 -> サンプル."""
    t = unicodedata.normalize("NFKC", text or "")
    t = BRANCH.sub("", LEGAL_FORMS.sub(" ", t))
    return re.sub(r"[\s　・.()（）]", "", t)


def split_one_field(full: str, surnames: set, min_rest: int = 1) -> tuple[str, str]:
    """A whole name in the surname field, split after the longest known surname."""
    for n in range(min(5, len(full) - min_rest), 0, -1):
        if full[:n] in surnames:
            return full[:n], full[n:]
    return full, ""


@dataclass
class Address:
    text: str  # normalized full text (prefecture dropped)
    locality: str  # municipality + town, current name
    house: str  # locality + block number (e.g. 1-27-17)
    unit: str  # house + room ("" without a room)


def address_parts(raw: str, towns: dict[str, str]) -> Address:
    """Parsed as a registry-based parser does: the longest known locality (towns: every name it
    has had -> its current name), then the block number and the building/room."""
    s = unicodedata.normalize("NFKC", raw or "").replace("　", " ").strip()
    for p in PREFECTURES:
        if s.startswith(p):
            s = s[len(p) :].lstrip()
            break
    s = re.sub(r"(?<=区)\S*?(通|筋)\S*?(上る|下る|入る|西入|東入)", "", s)  # Kyoto street style
    known = next((s[:n] for n in range(min(len(s), 40), 1, -1) if s[:n] in towns), None)
    if known:
        locality, rest = towns[known], s[len(known) :]
    else:  # not in the registry (a typo): up to the first number
        m = re.match(r"^([^0-9〇一二三四五六七八九十]+)(.*)$", s)
        locality, rest = (m.group(1), m.group(2)) if m else (s, "")
    rest = re.sub("[〇一二三四五六七八九十]+", lambda m: kansuji_number(m.group()), rest)
    rest = re.sub(r"丁目|番地|番|号(?!室)", "-", rest).replace("号室", "")
    rest = rest.replace("ー", "-").replace("−", "-").strip()
    m = re.match(r"^([\d-]*\d)?[-\s]*(.*)$", rest)
    block, building = (m.group(1) or "", m.group(2)) if m else ("", rest)
    room = "".join(c for c in building if c.isdigit())
    house = f"{locality}|{block}" if block else ""
    text = locality + block + building.replace(" ", "")
    return Address(text, locality, house, f"{house}|{room}" if house and room else "")


def load_towns(path: Path | None) -> dict[str, str]:
    """The town registry: every locality name, old and current -> its current name (TSV columns
    name, current)."""
    if not path or not Path(path).is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["name"]: r["current"] or r["name"] for r in csv.DictReader(f, delimiter="\t")}


@dataclass
class Record:
    key: str
    sur_k: str = ""
    giv_k: str = ""
    sur_n: str = ""
    giv_n: str = ""
    full_k: str = ""
    full_n: str = ""
    loose_n: str = ""  # full kana without dakuten (a guessed reading meets)
    dob: str = ""
    gender: str = ""
    aliases: set = field(default_factory=set)  # other surnames it states (a shop's base name)
    flag: str = ""  # the person/company flag as recorded (1 person, 2 company); not always right
    company_like: bool = False  # a legal form in the name
    shop_like: bool = False  # a trade word (a sole proprietor's shop, or a company)
    core_k: str = ""  # company name core (kanji)
    core_n: str = ""  # company name core (kana)
    imputed: bool = False  # kana filled from the learned readings
    phones: set = field(default_factory=set)
    houses: set = field(default_factory=set)
    units: set = field(default_factory=set)
    localities: set = field(default_factory=set)
    addr_texts: list = field(default_factory=list)


SETS = ("aliases", "phones", "houses", "units", "localities")


def record_row(r: Record) -> dict:
    row = asdict(r)
    for name in SETS:
        row[name] = sorted(row[name])
    return row


def record_of(row: dict) -> Record:
    return Record(**{**row, **{name: set(row.get(name) or ()) for name in SETS}})


def standardize(raw: list[dict], towns: dict[str, str], fixes=FIXES) -> list[Record]:
    """One standardized record per raw contact (the order kept)."""
    fx = set(fixes)
    recs = []
    for r in raw:
        rec = Record(r["key"])
        sk, gk = r.get("sur_kanji") or "", r.get("giv_kanji") or ""
        sn, gn = r.get("sur_kana") or "", r.get("giv_kana") or ""
        rec.flag = (r.get("flag") or "").strip()
        rec.company_like = bool(LEGAL_FORMS.search(unicodedata.normalize("NFKC", sk + " " + sn)))
        rec.shop_like = bool(SHOP_WORDS.search(sk))
        if "company_clean" in fx and (rec.company_like or rec.flag == "2"):
            sk = RECIPIENT.sub("", unicodedata.normalize("NFKC", sk)).strip()
            if DEPARTMENT.match(unicodedata.normalize("NFKC", gk).strip()):
                gk = ""  # a department or contact person, not a given name
        if rec.company_like or rec.shop_like or rec.flag == "2":
            rec.core_k, rec.core_n = kanji_key(company_core(sk)), kana_key(company_core(sn))
            if "company_clean" in fx:
                rec.core_k, rec.core_n = fold_company(rec.core_k), fold_company(rec.core_n)
        if (
            "shop_alias" in fx and rec.shop_like
        ):  # 藤江不動産: the owner's surname is the shop's base
            base = SHOP_WORDS.sub("", company_core(sk))
            if 1 <= len(base) <= 4:
                rec.aliases.add(kanji_key(base))
        if "kana_ocr" in fx:
            sn, gn = sn.translate(KANA_OCR), gn.translate(KANA_OCR)
        if is_kana(kata(sk + gk)):  # a kana-only kanji field is kana
            if not (sn or gn):
                sn, gn = sk, gk
            sk = gk = ""
        rec.sur_k, rec.giv_k = kanji_key(sk), kanji_key(gk)
        rec.sur_n, rec.giv_n = kana_key(sn), kana_key(gn)
        rec.dob = r.get("birth_date") or ""
        rec.gender = (r.get("gender") or "").strip()
        for text in r.get("phones") or ():
            if p := phone_key(text):
                rec.phones.add(p)
        for text in r.get("addresses") or ():
            text = (text or "").strip()
            if not text:
                continue
            a = address_parts(text, towns)
            rec.addr_texts.append(a.text)
            rec.localities.add(a.locality)
            if a.house:
                rec.houses.add(a.house)
            if a.unit:
                rec.units.add(a.unit)
        recs.append(rec)
    if "split_one_field" in fx:  # surnames the data itself knows (contacts with both fields)
        known_k = {
            s for s, c in Counter(x.sur_k for x in recs if x.sur_k and x.giv_k).items() if c >= 2
        }
        known_n = {
            s for s, c in Counter(x.sur_n for x in recs if x.sur_n and x.giv_n).items() if c >= 2
        }
        for x in recs:
            if x.sur_k and not x.giv_k and len(x.sur_k) >= 3:
                x.sur_k, x.giv_k = split_one_field(x.sur_k, known_k)
            if x.sur_n and not x.giv_n and len(x.sur_n) >= 4:
                x.sur_n, x.giv_n = split_one_field(x.sur_n, known_n, 2)
    if "readings" in fx:  # the data's own readings: kanji -> its usual kana, where it is clear
        for part in ("sur", "giv"):
            seen = defaultdict(Counter)
            for x in recs:
                k, n = getattr(x, part + "_k"), getattr(x, part + "_n")
                if k and n:
                    seen[k][n] += 1
            reading = {
                k: c.most_common(1)[0][0]
                for k, c in seen.items()
                if sum(c.values()) >= 2 and c.most_common(1)[0][1] / sum(c.values()) >= 0.6
            }
            for x in recs:
                k = getattr(x, part + "_k")
                if k and not getattr(x, part + "_n") and k in reading:
                    setattr(x, part + "_n", reading[k])
                    x.imputed = True
    for x in recs:
        x.full_k, x.full_n = x.sur_k + x.giv_k, x.sur_n + x.giv_n
        x.loose_n = seion(x.full_n)
    return recs


def frequencies(records: list[Record]) -> dict[str, Counter]:
    return {
        "sur_n": Counter(r.sur_n for r in records if r.sur_n),
        "giv_n": Counter(r.giv_n for r in records if r.giv_n),
        "full_n": Counter(r.full_n for r in records if r.full_n),
        "sur_k": Counter(r.sur_k for r in records if r.sur_k),
        "phone": Counter(p for r in records for p in r.phones),
        "house": Counter(h for r in records for h in r.houses),
        "dob": Counter(r.dob for r in records if r.dob),
    }
