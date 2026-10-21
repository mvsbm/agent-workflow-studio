"""Load the contact tables and standardize them into one record per CONT_ID.

Standardization levels (an experiment dimension):
  raw  - NFKC + katakana only.
  full - also: kanji variants (itaiji) folded, kana small->large, ヅ/ヂ -> ズ/ジ, long vowels folded,
         a kana-only kanji field moved to kana, phones canonical (digits, +81, a lost leading 0,
         placeholders dropped), addresses parsed to locality (renamed municipalities mapped to their
         current name) + block number + room.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Public knowledge a real system takes from a registry (異体字 tables, the address base registry's
# municipality history): kanji variants and renamed municipalities.
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
for row, v in zip(
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
    VOWEL |= dict.fromkeys(row, v)
KANSUJI = dict(zip("〇一二三四五六七八九", "0123456789", strict=True))


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


def kana_key(s: str, level: str) -> str:
    s = kata(s).replace(" ", "").replace("・", "").replace("　", "")
    if level == "raw":
        return s
    s = s.translate(SMALL).replace("ヅ", "ズ").replace("ヂ", "ジ")
    return fold_long(s)


def seion(s: str) -> str:
    """Without dakuten/handakuten: a clerk's unvoiced guess (ナカツカ for ナカヅカ) meets."""
    return "".join(unicodedata.normalize("NFD", c)[0] for c in s)


def kanji_key(s: str, level: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace(" ", "").replace("　", "")
    return s if level == "raw" else "".join(ITAIJI.get(c, c) for c in s)


def phone_key(s: str, level: str) -> str:
    digits = "".join(c for c in unicodedata.normalize("NFKC", s or "") if c.isdigit())
    if level == "raw":
        return digits
    if digits.startswith("81") and len(digits) in (11, 12):
        digits = "0" + digits[2:]
    if digits and digits[0] != "0" and len(digits) in (9, 10):
        digits = "0" + digits  # a lost leading 0
    if not digits or set(digits) <= {"0"} or len(digits) not in (10, 11):
        return ""
    return digits


@dataclass
class Address:
    text: str  # normalized full text (prefecture dropped)
    locality: str  # municipality + town, current name
    house: str  # locality + block number (e.g. 1-27-17)
    unit: str  # house + room ("" without a room)


def address_parts(raw: str, level: str, towns: dict[str, str]) -> Address:
    """Parsed as a registry-based parser does: the longest known locality (towns: every name it
    has had -> its current name), then the block number and the building/room."""
    s = unicodedata.normalize("NFKC", raw or "").replace("　", " ").strip()
    if level == "raw":
        return Address(s, s, s, s)
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


@dataclass
class Record:
    cont_id: str
    sur_k: str = ""
    giv_k: str = ""
    sur_n: str = ""
    giv_n: str = ""
    full_k: str = ""
    full_n: str = ""
    loose_n: str = ""  # full kana without dakuten (a guessed reading meets)
    dob: str = ""
    gender: str = ""
    aliases: set = field(default_factory=set)  # other surnames it states (a maiden-name note)
    flag: str = ""  # CONT_TYPE as recorded (1 person, 2 company); not always right
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
    name_text: str = ""  # for embeddings: kanji and kana
    addr_text: str = ""


def town_registry() -> dict[str, str]:
    """Every locality name, old and current -> its current name: the curated municipal mergers and,
    when cached, every town in Japan (Japan Post), as a registry-based parser has them."""
    from custom_nodes.agent_workflow_studio.nayose_bench.generate import PLACES
    from custom_nodes.agent_workflow_studio.nayose_bench.sources import CACHE

    towns = {loc: history[-1][1] for _, _, history, _ in PLACES for _, loc in history}
    if (CACHE / "towns.tsv").exists():
        with open(CACHE / "towns.tsv", encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                towns.setdefault(r["city"] + r["town"], r["city"] + r["town"])
    return towns


# The standardization fixes found by the stress test, each one switchable for the ablation.
FIXES = ("clean_fields", "kana_ocr", "split_one_field", "readings", "placeholder_dob", "locality_key", "shop_alias", "company_clean")
RECIPIENT = re.compile(r"\s*御中\s*|\s+(\S*(部|課|室)(\s*\S+様)?|ご担当者様|担当\s*\S*|\S+様)\s*$")
DEPARTMENT = re.compile(r"^(\S*(部|課|室)|担当.*|代表|ご担当者.*)$")
LOANWORD = str.maketrans({"ー": None, "・": None, "ヴ": "ブ", **dict(zip("ャュョァィゥェォッ", "ヤユヨアイウエオツ"))})


def fold_company(core: str) -> str:
    """A company core with loanword spellings folded: コンピューター/コンピュータ, ヴ/ブ, ティ/チ,
    キャノン/キヤノン, ・, ＆/アンド."""
    core = core.replace("ティ", "チ").replace("ディ", "ジ").replace("アンド", "&").replace("＆", "&")
    return core.translate(LOANWORD)
HONORIFIC = re.compile(r"\s*(様|殿|さん|サマ|ドノ)\s*$")
NOTE = re.compile(r"[（(【]\s*旧(?:姓)?\s*[】]?\s*([^）)】\s]+)\s*[）)】]?")
KANA_OCR = str.maketrans("八卜口二工力夕一", "ハトロニエカター")
GENDER = {"m": "M", "1": "M", "男": "M", "男性": "M", "f": "F", "2": "F", "女": "F", "女性": "F"}
PHONE_NOTE = re.compile(r"(内線|ext\.?|EXT\.?)\s*\d+|[（(][^）)0-9]*[）)]")  # notes, never digits


LEGAL_FORMS = re.compile(
    r"株式会社|有限会社|合同会社|合名会社|合資会社|一般社団法人|一般財団法人|医療法人|社会福祉法人|"
    r"[（(]\s*[株有同名資]\s*[）)]|㈱|㈲|カブシキガイシャ|ユウゲンガイシャ|ゴウドウガイシャ|"
    r"(?:^|\s)[カユドメシ]\)|\([カユドメシ](?:\s|$)"
)
BRANCH = re.compile(r"\s*\S{0,6}?(本社|本店|支店|営業所|出張所|工場|事業所)$")
SHOP_WORDS = re.compile(r"商店|工務店|美容室|理容店|歯科医院|クリニック|事務所|食堂|酒店|電器|農園|不動産|塗装|鍼灸院|教室")


def company_core(text: str) -> str:
    """A company name without its legal form, branch and spacing: 株式会社サンプル 東京支店 -> サンプル."""
    t = unicodedata.normalize("NFKC", text or "")
    t = BRANCH.sub("", LEGAL_FORMS.sub(" ", t))
    return re.sub(r"[\s　・.()（）]", "", t)


def clean_name(kanji: str) -> tuple[str, set]:
    """Without honorifics and notes; a maiden-name note becomes an alias surname."""
    aliases = {m.group(1) for m in NOTE.finditer(kanji)}
    kanji = HONORIFIC.sub("", NOTE.sub("", kanji)).strip()
    if re.fullmatch(r"[A-Za-z\s.]+", kanji):  # romaji typed into the kanji field
        kanji = ""
    return kanji, aliases


def phone_numbers(text: str) -> list[str]:
    """Every number in a phone field: notes and extensions dropped, two numbers split."""
    text = PHONE_NOTE.sub(" ", unicodedata.normalize("NFKC", text or ""))
    return [p for p in re.split(r"[,/・、;\s]+(?=[+0-9(])", text) if p.strip()]


def split_one_field(full: str, surnames: set, min_rest: int = 1) -> tuple[str, str]:
    """A whole name in the surname field, split after the longest known surname."""
    for n in range(min(5, len(full) - min_rest), 0, -1):
        if full[:n] in surnames:
            return full[:n], full[n:]
    return full, ""


def load(data_dir: Path, level: str = "full", fixes: tuple = ()) -> list[Record]:
    def rows(name):
        with open(data_dir / name, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    fx = set(fixes)
    towns = town_registry() if level != "raw" else {}
    records, raw = {}, rows("contacts.csv")
    for r in raw:
        rec = Record(r["CONT_ID"])
        sk, gk = r["LAST_NAME_KANJI"], r["FIRST_NAME_KANJI"]
        sn, gn = r["LAST_NAME_KANA"], r["FIRST_NAME_KANA"]
        rec.flag = (r.get("CONT_TYPE") or "").strip()
        rec.company_like = bool(LEGAL_FORMS.search(unicodedata.normalize("NFKC", sk + " " + sn)))
        rec.shop_like = bool(SHOP_WORDS.search(sk))
        if "company_clean" in fx and (rec.company_like or rec.flag == "2"):
            sk = RECIPIENT.sub("", unicodedata.normalize("NFKC", sk)).strip()
            if DEPARTMENT.match(unicodedata.normalize("NFKC", gk).strip()):
                gk = ""  # a department or contact person, not a given name
        if rec.company_like or rec.shop_like or rec.flag == "2":
            rec.core_k, rec.core_n = kanji_key(company_core(sk), level), kana_key(company_core(sn), level)
            if "company_clean" in fx:
                rec.core_k, rec.core_n = fold_company(rec.core_k), fold_company(rec.core_n)
        if "shop_alias" in fx and rec.shop_like:  # 藤江不動産: the owner's surname is the shop's base
            base = SHOP_WORDS.sub("", company_core(sk))
            if 1 <= len(base) <= 4:
                rec.aliases.add(kanji_key(base, level))
        if "clean_fields" in fx:
            (sk, a1), (gk, a2) = clean_name(sk), clean_name(gk)
            rec.aliases |= {kanji_key(a, level) for a in a1 | a2}
            sn, gn = HONORIFIC.sub("", sn), HONORIFIC.sub("", gn)
        if "kana_ocr" in fx:
            sn, gn = sn.translate(KANA_OCR), gn.translate(KANA_OCR)
        if level != "raw" and is_kana(kata(sk + gk)):  # a kana-only kanji field is kana
            if not (sn or gn):
                sn, gn = sk, gk
            sk = gk = ""
        rec.sur_k, rec.giv_k = kanji_key(sk, level), kanji_key(gk, level)
        rec.sur_n, rec.giv_n = kana_key(sn, level), kana_key(gn, level)
        rec.dob = r["BIRTH_DATE"]
        g = (r["GENDER"] or "").strip()
        rec.gender = GENDER.get(g.lower(), g) if "clean_fields" in fx else g
        rec.name_text = " ".join(x for x in (sk + " " + gk, sn + " " + gn) if x.strip())
        records[rec.cont_id] = rec
    recs = list(records.values())
    if "split_one_field" in fx:  # surnames the data itself knows (contacts with both fields)
        known_k = {s for s, c in Counter(x.sur_k for x in recs if x.sur_k and x.giv_k).items() if c >= 2}
        known_n = {s for s, c in Counter(x.sur_n for x in recs if x.sur_n and x.giv_n).items() if c >= 2}
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
    if "placeholder_dob" in fx:  # a date shared far beyond chance is a placeholder
        counts = Counter(x.dob for x in recs if x.dob)
        typical = sorted(counts.values())[len(counts) // 2] if counts else 1
        for x in recs:
            if x.dob and counts[x.dob] > max(8, 15 * typical):
                x.dob = ""
    for x in recs:
        x.full_k, x.full_n = x.sur_k + x.giv_k, x.sur_n + x.giv_n
        x.loose_n = seion(x.full_n) if level != "raw" else x.full_n
    for r in rows("phones.csv"):
        rec = records.get(r["CONT_ID"])
        if not rec:
            continue
        for text in phone_numbers(r["PHONE"]) if "clean_fields" in fx else [r["PHONE"]]:
            if p := phone_key(text, level):
                rec.phones.add(p)
    for r in rows("addresses.csv"):
        rec = records.get(r["CONT_ID"])
        text = (r["ADDRESS_KANJI"] or "").strip()
        if not rec or not text:
            continue
        if "clean_fields" in fx and (text in ("同上", "同右", "〃") or re.fullmatch(r"〒?\s*\d{3}-?\d{4}", text)):
            continue
        a = address_parts(text, level, towns)
        rec.addr_texts.append(a.text)
        rec.localities.add(a.locality)
        if a.house:
            rec.houses.add(a.house)
        if a.unit:
            rec.units.add(a.unit)
    for rec in recs:
        rec.addr_text = " ".join(rec.addr_texts[:3])
    return recs


def load_key(key_dir: Path) -> dict[str, str]:
    with open(key_dir / "answer_key_contacts.csv", encoding="utf-8") as f:
        return {r["record_id"]: r["entity_id"] for r in csv.DictReader(f)}


def load_tags(key_dir: Path) -> dict[str, set]:
    with open(key_dir / "answer_key_contacts.csv", encoding="utf-8") as f:
        return {r["record_id"]: set(filter(None, r["tags"].split(";"))) for r in csv.DictReader(f)}


def load_households(key_dir: Path) -> dict[str, str]:
    with open(key_dir / "entities.csv", encoding="utf-8") as f:
        return {r["entity_id"]: r["household"] for r in csv.DictReader(f)}


def true_pairs(records: list[Record], key: dict[str, str]) -> set[tuple[int, int]]:
    by = defaultdict(list)
    for i, r in enumerate(records):
        by[key[r.cont_id]].append(i)
    return {(a, b) for ids in by.values() for x, a in enumerate(ids) for b in ids[x + 1 :]}


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
