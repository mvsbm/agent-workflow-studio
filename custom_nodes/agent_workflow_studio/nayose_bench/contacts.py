"""A synthetic contact master, shaped like a CRM: contacts, phones and addresses joined on CONT_ID.

Built on generate.py's people (households, marriages, moves, twins, look-alike strangers). Each
person is registered as one or more contacts over the years, through different channels; each
contact holds the name as it was then (kanji, kana, and romaji generated from that kana), the birth
date (always filled), a gender (sometimes blank or wrong) and 1-23 phones and addresses. Every
field but CONT_ID and the birth date can be blank. A contact is one person; several contacts can
be the same person (the matching merges them).

Shared values make it hard on purpose: a household shares its landline and address, workplaces
share a switchboard number, care homes share an address. The answer key says which person each
contact is, in the scorer's layout (system "contacts", record_id = CONT_ID, role "contact").
Stdlib only, deterministic per seed.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import unicodedata
from pathlib import Path

from . import generate as g

D = g.D
CONTACT_COLUMNS = [
    "CONT_ID",
    "LAST_NAME_KANJI",
    "FIRST_NAME_KANJI",
    "LAST_NAME_KANA",
    "FIRST_NAME_KANA",
    "LAST_NAME_ROMAJI",
    "FIRST_NAME_ROMAJI",
    "BIRTH_DATE",
    "GENDER",
    "CONT_TYPE",  # 1 = person (個人), 2 = company (法人); agencies sometimes set it wrong
]
PHONE_COLUMNS = ["CONT_ID", "PHONE"]
ADDRESS_COLUMNS = ["CONT_ID", "ADDRESS_KANJI", "ADDRESS_KANA", "ADDRESS_ROMAJI"]
FILES = {"contacts": "contacts.csv", "phones": "phones.csv", "addresses": "addresses.csv"}

# Where a contact was registered: how its fields go bad (see generate.Damage).
CHANNELS = (["branch", "web", "phone", "paper", "legacy"], [4, 3, 2, 2, 1])
CONTACTS_PER_PERSON = ([1, 2, 3, 4, 5, 6, 7, 8], [55, 25, 11, 5, 2, 1, 0.6, 0.4])

PREF_KANA = {
    "東京都": "トウキョウト",
    "埼玉県": "サイタマケン",
    "静岡県": "シズオカケン",
    "長野県": "ナガノケン",
    "新潟県": "ニイガタケン",
    "大阪府": "オオサカフ",
    "京都府": "キョウトフ",
    "北海道": "ホッカイドウ",
    "宮城県": "ミヤギケン",
    "愛知県": "アイチケン",
    "神奈川県": "カナガワケン",
    "千葉県": "チバケン",
    "兵庫県": "ヒョウゴケン",
    "広島県": "ヒロシマケン",
    "福岡県": "フクオカケン",
}
LOCALITY_KANA = {
    "新宿区西新宿": "シンジュククニシシンジュク",
    "世田谷区経堂": "セタガヤクキョウドウ",
    "練馬区石神井町": "ネリマクシャクジイマチ",
    "田無市田無町": "タナシシタナシチョウ",
    "西東京市田無町": "ニシトウキョウシタナシチョウ",
    "保谷市東町": "ホウヤシヒガシチョウ",
    "西東京市東町": "ニシトウキョウシヒガシチョウ",
    "浦和市高砂": "ウラワシタカサゴ",
    "さいたま市高砂": "サイタマシタカサゴ",
    "さいたま市浦和区高砂": "サイタマシウラワクタカサゴ",
    "大宮市桜木町": "オオミヤシサクラギチョウ",
    "さいたま市桜木町": "サイタマシサクラギチョウ",
    "さいたま市大宮区桜木町": "サイタマシオオミヤクサクラギチョウ",
    "与野市上落合": "ヨノシカミオチアイ",
    "さいたま市上落合": "サイタマシカミオチアイ",
    "さいたま市中央区上落合": "サイタマシチュウオウクカミオチアイ",
    "川口市栄町": "カワグチシサカエチョウ",
    "清水市江尻町": "シミズシエジリチョウ",
    "静岡市江尻町": "シズオカシエジリチョウ",
    "静岡市清水区江尻町": "シズオカシシミズクエジリチョウ",
    "浜松市鍛冶町": "ハママツシカジマチ",
    "浜松市中区鍛冶町": "ハママツシナカクカジマチ",
    "浜松市中央区鍛冶町": "ハママツシチュウオウクカジマチ",
    "浜北市小松": "ハマキタシコマツ",
    "浜松市小松": "ハママツシコマツ",
    "浜松市浜北区小松": "ハママツシハマキタクコマツ",
    "浜松市浜名区小松": "ハママツシハマナクコマツ",
    "南安曇郡豊科町大字豊科": "ミナミアズミグントヨシナマチオオアザトヨシナ",
    "安曇野市豊科": "アズミノシトヨシナ",
    "南安曇郡穂高町大字穂高": "ミナミアズミグンホタカマチオオアザホタカ",
    "安曇野市穂高": "アズミノシホタカ",
    "新津市新津本町": "ニイツシニイツホンチョウ",
    "新潟市新津本町": "ニイガタシニイツホンチョウ",
    "新潟市秋葉区新津本町": "ニイガタシアキハクニイツホンチョウ",
    "大阪市北区梅田": "オオサカシキタクウメダ",
    "堺市大浜北町": "サカイシオオハマキタマチ",
    "堺市堺区大浜北町": "サカイシサカイクオオハマキタマチ",
    "京都市中京区二条殿町": "キョウトシナカギョウクニジョウドノチョウ",
    "札幌市中央区北一条西": "サッポロシチュウオウクキタイチジョウニシ",
    "仙台市青葉区一番町": "センダイシアオバクイチバンチョウ",
    "名古屋市中区栄": "ナゴヤシナカクサカエ",
    "横浜市港北区日吉": "ヨコハマシコウホククヒヨシ",
    "川崎市中原区小杉町": "カワサキシナカハラクコスギマチ",
    "船橋市本町": "フナバシシホンチョウ",
    "神戸市中央区三宮町": "コウベシチュウオウクサンノミヤチョウ",
    "広島市中区紙屋町": "ヒロシマシナカクカミヤチョウ",
    "福岡市博多区博多駅前": "フクオカシハカタクハカタエキマエ",
}
BUILDING_KANA = {
    "コーポ山田": "コーポヤマダ",
    "メゾン桜": "メゾンサクラ",
    "ハイツ松風": "ハイツマツカゼ",
    "レジデンス栄": "レジデンスサカエ",
}

# --- romaji, generated from the kana as the CRM does (Hepburn, upper case) ---------------------
_SINGLE = dict(
    zip(
        "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
        "ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴァィゥェォ",
        "a i u e o ka ki ku ke ko sa shi su se so ta chi tsu te to na ni nu ne no ha hi fu he ho "
        "ma mi mu me mo ya yu yo ra ri ru re ro wa o n ga gi gu ge go za ji zu ze zo da ji zu de do "
        "ba bi bu be bo pa pi pu pe po vu a i u e o".split(),
        strict=True,
    )
)
_DIGRAPH = {
    k + s: v
    for k, base in {
        "キ": "ky",
        "シ": "sh",
        "チ": "ch",
        "ニ": "ny",
        "ヒ": "hy",
        "ミ": "my",
        "リ": "ry",
        "ギ": "gy",
        "ジ": "j",
        "ヂ": "j",
        "ビ": "by",
        "ピ": "py",
    }.items()
    for s, v in (("ャ", base + "a"), ("ュ", base + "u"), ("ョ", base + "o"))
} | {
    "ファ": "fa",
    "フィ": "fi",
    "フェ": "fe",
    "フォ": "fo",
    "ティ": "ti",
    "ディ": "di",
    "シェ": "she",
    "ジェ": "je",
    "チェ": "che",
    "ウィ": "wi",
    "ウェ": "we",
    "ウォ": "wo",
}


def romaji(kana: str) -> str:
    """Hepburn romaji of a kana text (half-width and hiragana accepted), upper case; other
    characters (digits, spaces, hyphens) pass through, long-vowel marks are dropped."""
    text = unicodedata.normalize("NFKC", kana)
    text = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)
    out, i, double = [], 0, False
    while i < len(text):
        two, one = text[i : i + 2], text[i]
        if one == "ッ":
            double, i = True, i + 1
            continue
        if one == "ー":
            i += 1
            continue
        piece = _DIGRAPH.get(two)
        step = 2 if piece else 1
        piece = piece or _SINGLE.get(one, one)
        if double and piece[:1].isalpha() and piece[0] not in "aiueon":
            piece = ("t" if piece.startswith("ch") else piece[0]) + piece
        double = False
        out.append(piece)
        i += step
    return "".join(out).upper().replace("　", " ")


# --- one contact -------------------------------------------------------------------------------
def pareto_count(rng: random.Random, alpha: float, cap: int = 23) -> int:
    """1-23, most contacts holding one or two, a long tail holding many (P(n >= k) = k^-alpha)."""
    return min(cap, max(1, int(rng.paretovariate(alpha))))


def contact_name(dmg: g.Damage, person: dict, when: D, miss: float = 1.0) -> dict:
    """The six name fields as this contact holds them."""
    rng = dmg.rng
    ks, kg, ns, ng = g.name_parts(dmg, person, when)
    if any(g.VARIANT_TO_COMMON.get(c) for c in ks) and rng.random() < 0.35:
        ks = "".join(g.VARIANT_TO_COMMON.get(c, c) for c in ks)
        dmg.tags.add("variant_normalized")
    if dmg.hit(0.03, "itaiji"):
        ks = "".join(g.COMMON_TO_VARIANT.get(c, c) for c in ks)
    if dmg.hit(0.2 if dmg.channel == "phone" else 0.02):  # the wrong kanji for a name only heard
        group = [x for x in next((h for h in g.SURNAME_HOMOPHONES if ks in h), []) if x != ks]
        options = [o for o in next((x[3] for x in g.GIVEN if x[0] == ng), []) if o != kg]
        if group and (rng.random() < 0.4 or not options):
            ks = rng.choice(group)
            dmg.tags.add("homophone")
        elif options:
            kg = rng.choice(options)
            dmg.tags.add("homophone")
    if dmg.hit(0.12 if dmg.channel == "phone" else 0.02, "kana_only"):
        script = g.hira if rng.random() < 0.4 else str
        ks, kg = script(ns), script(ng)
    ks, kg = dmg.typo(dmg.ocr(ks), 0.01), dmg.typo(dmg.ocr(kg), 0.01)
    if dmg.hit(0.08 if dmg.channel in ("web", "phone") else 0.03, "long_vowel"):
        for lv in g.LONG_VOWELS:
            if lv in ns + ng:
                ns, ng = ns.replace(lv, lv[0] + "ー", 1), ng.replace(lv, lv[0] + "ー", 1)
                break
    for a, b in (("ヅ", "ズ"), ("ヂ", "ジ")):  # same sound, two spellings
        if a in ns + ng and dmg.hit(0.3, "kana_variant"):
            ns, ng = ns.replace(a, b), ng.replace(a, b)
    if dmg.channel == "web" and rng.random() < 0.3:
        ns, ng = g.hira(ns), g.hira(ng)
    ns, ng = dmg.typo(dmg.ocr(ns), 0.015), dmg.typo(dmg.ocr(ng), 0.015)
    if dmg.channel == "legacy":  # half-width kana, no small kana
        if (ns + ng).translate(g.NO_SMALL) != ns + ng:
            dmg.tags.add("no_small_kana")
        ns, ng = g.half(ns.translate(g.NO_SMALL)), g.half(ng.translate(g.NO_SMALL))
    if dmg.hit(0.01, "name_swapped"):  # surname typed into the given-name field and back
        ks, kg, ns, ng = kg, ks, ng, ns
    if dmg.hit(0.025 * miss, "kanji_missing"):
        ks = kg = ""
    elif dmg.hit(0.015 * miss, "given_missing"):
        kg = ""
    if dmg.hit(0.05 * miss, "kana_missing"):
        ns = ng = ""
    return {
        "LAST_NAME_KANJI": ks,
        "FIRST_NAME_KANJI": kg,
        "LAST_NAME_KANA": ns,
        "FIRST_NAME_KANA": ng,
        # Generated by the system from the kana, dirt and all.
        "LAST_NAME_ROMAJI": romaji(ns),
        "FIRST_NAME_ROMAJI": romaji(ng),
    }


def contact_dob(dmg: g.Damage, born: D) -> str:
    """Always filled (a date column): sometimes day and month swapped, or one digit wrong."""
    d = born
    if d.day <= 12 and d.day != d.month and dmg.hit(0.015, "dob_swapped"):
        d = D(d.year, d.day, d.month)
    text = d.isoformat()
    if dmg.hit(0.01, "dob_typo"):
        for _ in range(5):  # the column holds dates only: a typo that is still a date
            typo = dmg.digit_typo(text)
            try:
                dt.date.fromisoformat(typo)
                return typo
            except ValueError:
                continue
    return text


def contact_address(dmg: g.Damage, address: tuple, when: D, miss: float = 1.0, world=None) -> dict:
    rng = dmg.rng
    if isinstance(address[0], tuple):  # a real town (RealWorld)
        return real_address(dmg, world.src, address, miss)
    kanji, _ = g.render_address(dmg, address, when)
    place, numbers, building, _ = address
    pref, _, history, _ = g.PLACES[place]
    locality = g.at([(D(max(y, 1), 1, 1), loc) for y, loc in history], when)
    number = "-".join(str(n) for n in numbers)
    room = ""
    if building:
        name = building[:-3]
        room = BUILDING_KANA.get(name, name) + " " + building[-3:]
    kana = ("" if rng.random() < 0.25 else PREF_KANA[pref]) + LOCALITY_KANA[locality]
    kana = f"{kana} {number}" + (f" {room}" if room and rng.random() < 0.6 else "")
    if dmg.channel == "legacy":
        kana = g.half(kana.translate(g.NO_SMALL))
    if dmg.hit(0.15 * miss, "address_kana_missing"):
        kana = ""
    return {"ADDRESS_KANJI": kanji, "ADDRESS_KANA": kana, "ADDRESS_ROMAJI": romaji(kana)}


def block_number(dmg: g.Damage, numbers: tuple) -> str:
    rng, style = dmg.rng, dmg.rng.random()
    if len(numbers) == 3:
        c, b, n = numbers
        text = (
            f"{c}-{b}-{n}"
            if style < 0.45
            else f"{c}丁目{b}番{n}号"
            if style < 0.7
            else f"{g.kansuji(c)}丁目{g.kansuji(b)}番{g.kansuji(n)}号"
            if style < 0.82
            else f"{c}丁目{b}-{n}"
        )
        if dmg.hit(0.04, "address_partial"):
            text = f"{c}丁目" if rng.random() < 0.5 else ""
    elif len(numbers) == 2:
        text = f"{numbers[0]}-{numbers[1]}" if style < 0.6 else f"{numbers[0]}番地{numbers[1]}"
    else:
        text = f"{numbers[0]}番地" if style < 0.6 else str(numbers[0])
    return text.translate(g.WIDE) if rng.random() < 0.25 else text


def real_address(dmg: g.Damage, src, address: tuple, miss: float = 1.0) -> dict:
    """A real town (Japan Post: kanji and kana) at a synthetic block number."""
    rng = dmg.rng
    (_, idx), numbers, building, _ = address
    t = src.towns[idx]
    room = building if building and rng.random() < 0.6 else ""
    if room and rng.random() < 0.3:
        room = room[:-3] + " " + room[-3:] + "号室"
    kanji = (
        ("" if dmg.hit(0.25) else t["pref"])
        + t["city"]
        + rng.choice(["", "", " ", "　"])
        + t["town"]
        + block_number(dmg, numbers)
        + (rng.choice(["", " ", "　"]) + room if room else "")
    )
    kanji = dmg.typo(dmg.ocr(kanji, 0.3), 0.02)
    room_kana = ""
    if building:
        room_kana = BUILDING_KANA.get(building[:-3], building[:-3]) + " " + building[-3:]
    kana = ("" if rng.random() < 0.25 else t["pref_kana"]) + t["city_kana"] + t["town_kana"]
    kana = f"{kana} {'-'.join(str(n) for n in numbers)}" + (
        f" {room_kana}" if room_kana and rng.random() < 0.6 else ""
    )
    if dmg.channel == "legacy":
        kana = g.half(kana.translate(g.NO_SMALL))
    if dmg.hit(0.15 * miss, "address_kana_missing"):
        kana = ""
    return {"ADDRESS_KANJI": kanji, "ADDRESS_KANA": kana, "ADDRESS_ROMAJI": romaji(kana)}


def office_address(dmg: g.Damage, src, head: dict) -> dict:
    """A company's real head office (National Tax Agency registry), as staff typed it."""
    rng = dmg.rng
    street = head["street"]
    if rng.random() < 0.4:  # 二丁目8番1号 -> 2-8-1
        street = re.sub(r"[〇一二三四五六七八九十]+", lambda m: _kanji_number(m.group()), street)
        street = re.sub(r"丁目|番地|番|号", "-", street).rstrip("-")
    kanji = dmg.typo(("" if dmg.hit(0.2) else head["pref"]) + head["city"] + street, 0.02)
    town = next(
        (
            src.towns[i]
            for i in src.towns_by_city.get((head["pref"], head["city"]), [])
            if head["street"].startswith(src.towns[i]["town"])
        ),
        None,
    )
    kana = (town["city_kana"] + town["town_kana"]) if town and rng.random() < 0.7 else ""
    if kana and dmg.channel == "legacy":
        kana = g.half(kana)
    return {"ADDRESS_KANJI": kanji, "ADDRESS_KANA": kana, "ADDRESS_ROMAJI": romaji(kana)}


def _kanji_number(s: str) -> str:
    digits = dict(zip("〇一二三四五六七八九", "0123456789", strict=True))
    if "十" not in s:
        return "".join(digits[c] for c in s)
    tens, _, ones = s.partition("十")
    if len(tens) > 1 or len(ones) > 1:
        return "".join(digits.get(c, "") for c in s)
    return str(int(digits[tens] if tens else 1) * 10 + (int(digits[ones]) if ones else 0))


SMALL_TO_BIG = str.maketrans("ャュョァィゥェォッ", "ヤユヨアイウエオツ")
BIG_TO_SMALL = str.maketrans("ヤユヨ", "ャュョ")
ENGLISH_FORMS = ["Co., Ltd.", "Co.,Ltd.", "CO., LTD.", "Inc.", "K.K.", "Corporation", "Corp.", ""]


def loanword(rng, text: str) -> str:
    """Katakana loanwords as people spell them: a final long vowel dropped or added
    (コンピューター/コンピュータ), ヴ as ブ, ティ as チ, big and small ャ swapped (キヤノン/キャノン),
    ・ and ＆ written differently."""
    kind = rng.random()
    if kind < 0.3:
        return (
            re.sub(r"([ァ-ヴ])ー(?=[^ァ-ヴー]|$)", r"\1", text)
            if "ー" in text
            else re.sub(r"([ァ-ヴ]{3,})(?=[^ァ-ヴ]|$)", r"\1ー", text, count=1)
        )
    if kind < 0.45:
        return text.replace("ヴィ", "ビ").replace("ヴァ", "バ").replace("ヴ", "ブ")
    if kind < 0.6:
        return text.replace("ティ", "チ").replace("ディ", "ジ")
    if kind < 0.8:
        return (
            text.translate(SMALL_TO_BIG)
            if any(c in text for c in "ャュョ")
            else text.translate(BIG_TO_SMALL)
        )
    return (
        text.replace("・", "")
        if "・" in text
        else text.replace("＆", "アンド").replace("&", "アンド")
    )


def company_name(dmg: g.Damage, c: dict, name: str, kana_name: str) -> dict:
    """The six name fields of a company contact as staff typed it: legal form full/short/symbol/
    omitted/moved, a branch, loanword and width variants, sometimes the English name; 御中 or a
    department and person; kana with or without the legal form, or as a bank-transfer name."""
    from .real import LEGAL_KANA, LEGAL_SHORT

    rng, legal = dmg.rng, c["legal"]
    front = name.startswith(legal)
    core = name.replace(legal, "", 1).strip() if legal in name else name
    if dmg.hit(0.10, "loanword_variant"):
        core = loanword(rng, core)
    if rng.random() < 0.4:  # Ａ-Ｚ as A-Z
        core = core.translate(
            str.maketrans({chr(c): chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)})
        )
    style = rng.random()
    short = LEGAL_SHORT.get(legal, ("", "", ""))
    form = legal if style < 0.5 else short[0] if style < 0.72 else short[1] if style < 0.82 else ""
    if not form:
        dmg.tags.add("legal_form_omitted")
    if dmg.hit(0.04, "legal_form_moved"):
        front = not front
    kanji = (form + core) if front else (core + form)
    if c.get("english") and dmg.hit(0.06, "english_name"):
        kanji = (c["english"] + " " + rng.choice(ENGLISH_FORMS)).strip()
    if rng.random() < 0.25:
        kanji += rng.choice(
            [" 本社", " 本店", " 営業所", " 支店", " 東京支店", " 大阪支店", " 名古屋営業所"]
        )
        dmg.tags.add("company_branch")
    if dmg.hit(0.08, "onchu"):
        kanji += rng.choice([" 御中", "　御中", "御中"])
    elif dmg.hit(0.03, "recipient_person"):
        kanji += rng.choice([" 営業部 山田様", " 総務部御中", " ご担当者様", " 経理課 佐藤様"])
    kanji = dmg.typo(dmg.ocr(kanji), 0.01)
    legal_kana = LEGAL_KANA.get(legal, "")
    kana_style = rng.random()
    kana = (
        kana_name
        if kana_style < 0.55
        else (legal_kana + kana_name if front else kana_name + legal_kana)
        if kana_style < 0.8
        else g.hira(kana_name)
    )
    if dmg.hit(
        0.08, "zengin_kana"
    ):  # as on a bank transfer: half-width, no small kana, a code, 30 characters
        code = short[2] or ""
        kana = g.half(
            ((code + kana_name) if front else (kana_name + "(" + code[:1])).translate(g.NO_SMALL)
        )[:30]
    elif dmg.channel == "legacy":
        kana = g.half(kana.translate(g.NO_SMALL))
    if dmg.hit(0.08, "kana_missing"):
        kana = ""
    first = ""
    if dmg.hit(0.06, "company_department"):
        first = rng.choice(["営業部", "総務課", "経理部", "購買部", "担当 山田", "代表"])
    return {
        "LAST_NAME_KANJI": kanji,
        "FIRST_NAME_KANJI": first,
        "LAST_NAME_KANA": kana,
        "FIRST_NAME_KANA": "",
        "LAST_NAME_ROMAJI": romaji(kana),
        "FIRST_NAME_ROMAJI": "",
    }


def company_contact(rng, c: dict, world, damage: float, miss: float) -> tuple:
    """One contact of a company, made between its founding and its closure: the name and head
    office it had then."""
    when = g.rand_date(rng, max(g.START, c["founded"]), c.get("closed") or g.END)
    channel = rng.choices(*CHANNELS)[0]
    dmg = g.Damage(rng, rng.choice(g.AGENCY_SLOPPINESS), channel, damage)
    dmg.tags.add("company")
    _, name, kana = (
        [x for x in c["names"] if x[0] <= when][-1] if c["names"][0][0] <= when else c["names"][0]
    )
    if name != c["names"][-1][1]:
        dmg.tags.add("old_company_name")
    head = (
        [h for d, h in c["heads"] if d <= when][-1]
        if c["heads"][0][0] <= when
        else c["heads"][0][1]
    )
    if head is not c["heads"][-1][1]:
        dmg.tags.add("old_head_office")
    row = company_name(dmg, c, name, kana)
    style = rng.random()  # most hold the founding date
    if style < 0.70:
        row["BIRTH_DATE"] = contact_dob(dmg, c["founded"])
    elif style < 0.80:
        row["BIRTH_DATE"] = f"{c['founded'].year}-01-01"
        dmg.tags.add("founding_year_only")
    elif style < 0.85:
        row["BIRTH_DATE"] = when.isoformat()
        dmg.tags.add("registration_date")
    else:
        row["BIRTH_DATE"] = ""
        dmg.tags.add("company_no_date")
    row["GENDER"] = (
        rng.choice(["M", "F", "1", "2"]) if dmg.hit(0.04, "company_gender_filled") else ""
    )
    row["CONT_TYPE"] = "1" if dmg.hit(0.04, "type_flag_wrong") else "2"
    numbers = rng.sample(c["phones"], min(len(c["phones"]), rng.choice([1, 1, 2, 3])))
    if c.get("fax") and dmg.hit(0.05, "fax_in_phone"):
        numbers.append(c["fax"])
    if c.get("tollfree") and rng.random() < 0.5:
        numbers.append(c["tollfree"])
    phones = []
    for number in numbers:
        if (text := g.render_phone(dmg, number)) and text not in phones:
            phones.append(text)
    addresses = [office_address(dmg, world.src, head)]
    for b in c["branches"]:
        if rng.random() < 0.5:
            addresses.append(contact_address(dmg, b, when, miss, world))
    return c, row, phones, addresses, dmg.tags


def shop_name(dmg: g.Damage, p: dict) -> dict:
    """A sole proprietor's contact registered under the shop's name."""
    shop = p["shop"]
    kanji = dmg.typo(dmg.ocr(shop["name"]), 0.01)
    first = (
        p["given"][0] if dmg.rng.random() < 0.2 else ""
    )  # the owner's given name next to the shop
    kana = shop["kana"] if dmg.rng.random() < 0.85 else ""
    if dmg.channel == "legacy":
        kana = g.half(kana.translate(g.NO_SMALL))
    return {
        "LAST_NAME_KANJI": kanji,
        "FIRST_NAME_KANJI": first,
        "LAST_NAME_KANA": kana,
        "FIRST_NAME_KANA": "",
        "LAST_NAME_ROMAJI": romaji(kana),
        "FIRST_NAME_ROMAJI": "",
    }


class Pools:
    """Each person's phones and addresses over the years, grown as contacts need more; plus
    numbers and addresses many people share (workplace switchboards, care homes)."""

    def __init__(self, world: g.World, rng: random.Random):
        self.world, self.rng = world, rng
        people = world.people
        self.switchboards = [
            world.unique(lambda: f"03-{rng.randint(5000, 6999)}-{rng.randint(0, 9999):04d}")
            for _ in range(max(5, len(people) // 150))
        ]
        self.care_homes = [world.address(None, g.START) for _ in range(max(3, len(people) // 400))]
        self.phones: dict[str, list] = {}
        self.addresses: dict[str, list] = {}
        for p in people:
            phones = [(w, n) for w, n in p["mobile"] if n]
            phones += [(w, a[3]) for w, a in p["addr"] if a[3]]
            if rng.random() < 0.04:
                phones.append((g.START, rng.choice(self.switchboards)))
            addresses = [(max(w, D(1950, 1, 1)), a) for w, a in p["addr"]]
            if p["dob"] < D(1945, 1, 1) and rng.random() < 0.3:
                addresses.append((D(2015, 1, 1), rng.choice(self.care_homes)))
            self.phones[p["id"]], self.addresses[p["id"]] = phones, addresses

    def take(self, pool: list, n: int, when: D, extra) -> list:
        """n values for a contact made on `when`: what was current then first, then any other."""
        while len(pool) < n:
            pool.append(extra())
        known = [v for since, v in pool if since <= when] or [v for _, v in pool]
        current = known[-1:]
        others = [v for _, v in pool if v not in current]
        self.rng.shuffle(others)
        return list(dict.fromkeys(current + others))[:n]

    def extra_phone(self) -> tuple:
        rng = self.rng
        number = self.world.unique(
            lambda: f"0{rng.choice('987')}0-{rng.randint(1000, 9999)}-{rng.randint(0, 9999):04d}"
        )
        return (g.rand_date(rng, g.START, g.END), number)

    def extra_address(self) -> tuple:
        when = g.rand_date(self.rng, g.START, g.END)
        return (when, self.world.address(None, when))


GENDER_CODES = [("M", "F"), ("1", "2"), ("男", "女"), ("男性", "女性")]


def wild(rng, rate: float, p: dict, row: dict, phones: list, addresses: list, tags: set) -> None:
    """CRM mess beyond the dirt model: notes and honorifics in name fields, a whole name in one
    field, romaji in the kanji field, gender codes of several systems, placeholder birth dates,
    extensions and two numbers in a phone field, "same as above" and postal-code-only addresses."""

    def hit(base, tag):
        if rng.random() < base * rate:
            tags.add(tag)
            return True
        return False

    if row["FIRST_NAME_KANJI"] and hit(0.04, "wild_honorific"):
        row["FIRST_NAME_KANJI"] += rng.choice(["様", "殿", " 様"])
    if len(p["names"]) > 1 and row["LAST_NAME_KANJI"] and hit(0.15, "wild_maiden_note"):
        row["LAST_NAME_KANJI"] += (
            rng.choice(["（旧姓 ", "(旧", "【旧姓】"])
            + p["names"][0][1][0]
            + ("）" if rng.random() < 0.7 else "")
        )
    if hit(0.04, "wild_one_field"):  # the whole name typed into the surname field
        for script in ("KANJI", "KANA"):
            last, first = f"LAST_NAME_{script}", f"FIRST_NAME_{script}"
            sep = rng.choice(["", " ", "　"])
            row[last], row[first] = (row[last] + sep + row[first]).strip(), ""
        row["LAST_NAME_ROMAJI"], row["FIRST_NAME_ROMAJI"] = romaji(row["LAST_NAME_KANA"]), ""
    if row["LAST_NAME_KANA"] and hit(0.02, "wild_romaji_in_kanji"):
        row["LAST_NAME_KANJI"], row["FIRST_NAME_KANJI"] = (
            row["LAST_NAME_ROMAJI"],
            row["FIRST_NAME_ROMAJI"],
        )
    if (
        row["LAST_NAME_KANJI"]
        and len(row["LAST_NAME_KANJI"]) >= 2
        and hit(0.03, "wild_inner_space")
    ):
        k = row["LAST_NAME_KANJI"]
        row["LAST_NAME_KANJI"] = k[0] + rng.choice([" ", "　"]) + k[1:]
    if row["GENDER"] and hit(0.3, "wild_gender_code"):
        codes = rng.choice(GENDER_CODES)
        row["GENDER"] = codes[0] if row["GENDER"] == "M" else codes[1]
    if hit(0.015, "dob_placeholder"):
        row["BIRTH_DATE"] = rng.choice(["1900-01-01", "1901-01-01", "2000-01-01"])
    for i, ph in enumerate(phones):
        if hit(0.05, "wild_phone_note"):
            phones[i] = ph + rng.choice(
                ["（携帯）", " 内線" + str(rng.randint(100, 999)), "(自宅)", " ext.12"]
            )
    if len(phones) >= 2 and hit(0.06, "wild_two_phones"):
        phones[:2] = [phones[0] + rng.choice([", ", "/", "・", " "]) + phones[1]]
    if len(addresses) >= 2 and hit(0.05, "wild_same_as_above"):
        addresses[1] = {"ADDRESS_KANJI": "同上", "ADDRESS_KANA": "", "ADDRESS_ROMAJI": ""}
    if addresses and hit(0.02, "wild_postal_only"):
        addresses[-1] = {
            "ADDRESS_KANJI": f"〒{rng.randint(100, 999)}-{rng.randint(0, 9999):04d}",
            "ADDRESS_KANA": "",
            "ADDRESS_ROMAJI": "",
        }


class CommonNamesWorld(g.World):
    """A population heavy in common names, as at real scale: strangers sharing a name and a
    birth date are no longer rare."""

    def surname(self) -> tuple:
        if self.rng.random() < 0.15:
            return super().surname()
        kanji, kana = self.rng.choices(
            g.SURNAMES, [1 / (r + 1) ** 1.1 for r in range(len(g.SURNAMES))]
        )[0]
        return (kanji, kana, kana)

    def given(self, sex: str, born: D) -> tuple:
        if self.rng.random() < 0.15:
            return super().given(sex, born)
        era = "o" if born.year < 1955 else "m" if born.year < 1985 else "y"
        kana, _, _, kanji = self.rng.choice([x for x in g.GIVEN if x[1] == sex and era in x[2]])
        return (kanji[0], kana)


PROFILES = {
    "base": {},
    "dirty": {"damage": 2.5},
    "sparse": {"missing": 4.0},
    "lookalike": {"lookalike_rounds": 5, "common_names": True},
    "wild": {"wild": 1.0},
    "hard": {
        "damage": 2.0,
        "missing": 3.0,
        "lookalike_rounds": 4,
        "common_names": True,
        "wild": 1.0,
    },
    # Real names (Nemotron-Personas-Japan + JMnedict), real towns (Japan Post), real companies (NTA)
    # and sole proprietors; needs `python -m ...nayose_bench.sources` run once.
    "real": {"real": True, "companies": 0.12, "shops": 0.08},
    # Company-heavy: about a third of contacts are companies, with the registry's real events.
    "company": {"real": True, "companies": 0.35, "shops": 0.08},
    "company_hard": {
        "real": True,
        "companies": 0.35,
        "shops": 0.08,
        "damage": 2.0,
        "missing": 2.0,
        "wild": 1.0,
    },
    "real_hard": {
        "real": True,
        "companies": 0.12,
        "shops": 0.08,
        "damage": 2.0,
        "missing": 3.0,
        "lookalike_rounds": 4,
        "wild": 1.0,
    },
}


def make_contacts(
    world: g.World, seed: int = 7, damage: float = 1.0, profile: dict | None = None
) -> tuple[dict, list]:
    """The three tables' rows and the answer key."""
    profile = profile or {}
    miss, wild_rate = profile.get("missing", 1.0), profile.get("wild", 0.0)
    rng = random.Random(f"contacts-{seed}")
    pools = Pools(world, rng)
    made = []
    for p in world.people:
        count = rng.choices(*CONTACTS_PER_PERSON)[0]
        first = max(
            g.START, p["dob"], g.plus_years(p["dob"], 16) if rng.random() < 0.9 else p["dob"]
        )
        if first >= g.END:
            first = max(g.START, p["dob"])
        for when in sorted(g.rand_date(rng, first, g.END) for _ in range(count)):
            channel = rng.choices(*CHANNELS)[0]
            dmg = g.Damage(rng, rng.choice(g.AGENCY_SLOPPINESS), channel, damage)
            shop = (
                p.get("shop")
                if p.get("shop") and when >= p["shop"]["founded"] and rng.random() < 0.5
                else None
            )
            if shop:  # registered under the shop's name: either flag, either date
                dmg.tags.add("sole_proprietor_shop")
                row = shop_name(dmg, p)
                row["BIRTH_DATE"] = contact_dob(
                    dmg, p["dob"] if rng.random() < 0.5 else shop["founded"]
                )
                row["GENDER"] = p["sex"] if rng.random() < 0.4 else ""
                row["CONT_TYPE"] = "2" if rng.random() < 0.7 else "1"
            else:
                row = contact_name(dmg, p, when, miss)
                row["BIRTH_DATE"] = contact_dob(dmg, p["dob"])
                gender = p["sex"]
                if dmg.hit(0.02, "gender_wrong"):
                    gender = "F" if gender == "M" else "M"
                row["GENDER"] = "" if dmg.hit(0.10 * miss, "gender_missing") else gender
                row["CONT_TYPE"] = "2" if dmg.hit(0.01, "type_flag_wrong") else "1"
            phones = []
            if not dmg.hit(0.08 * miss, "no_phone"):
                n = pareto_count(rng, 1.25)
                numbers = pools.take(pools.phones[p["id"]], n, when, pools.extra_phone)
                for number in ([shop["phone"]] if shop else []) + numbers:
                    text = g.render_phone(dmg, number)
                    if text and text not in phones:
                        phones.append(text)
            addresses = []
            if not dmg.hit(0.05 * miss, "no_address"):
                n = pareto_count(rng, 1.5)
                taken = pools.take(pools.addresses[p["id"]], n, when, pools.extra_address)
                for address in ([shop["address"]] if shop else []) + taken:
                    addresses.append(contact_address(dmg, address, when, miss, world))
            if len(phones) > 5 or len(addresses) > 5:
                dmg.tags.add("many_values")
            if wild_rate:
                wild(rng, wild_rate, p, row, phones, addresses, dmg.tags)
            made.append((p, row, phones, addresses, dmg.tags))
    for c in getattr(world, "firms", []):  # companies: one to six contacts each
        for _ in range(rng.choices([1, 2, 3, 4, 5, 6], [45, 25, 14, 8, 5, 3])[0]):
            made.append(company_contact(rng, c, world, damage, miss))
    rng.shuffle(made)  # contact ids say nothing about who is who
    tables = {"contacts": [], "phones": [], "addresses": []}
    key = []
    for i, (p, row, phones, addresses, tags) in enumerate(made):
        cont_id = f"{1_000_001 + i * 7}"
        tables["contacts"].append({"CONT_ID": cont_id, **row})
        tables["phones"] += [{"CONT_ID": cont_id, "PHONE": ph} for ph in phones]
        tables["addresses"] += [{"CONT_ID": cont_id, **a} for a in addresses]
        entity = p["id"]
        if p.get("successor") and profile.get("merger_policy") == "successor":
            entity = p["successor"]  # the absorbed company's contacts belong to its successor
        key.append(
            {
                "system": "contacts",
                "record_id": cont_id,
                "role": "contact",
                "entity_id": entity,
                "tags": ";".join(sorted(set(tags) | set(p["tags"]))),
            }
        )
    return tables, key


def generate_contacts(
    data_dir: Path,
    key_dir: Path,
    contacts: int = 10_000,
    seed: int = 7,
    damage: float = 1.0,
    profile: str | dict = "base",
) -> dict:
    """About `contacts` CONT_IDs: the three tables to data_dir, the answer key to key_dir. A profile
    (PROFILES) makes it harder: more dirt, more blanks, more look-alike strangers, wild CRM mess."""
    profile = PROFILES[profile] if isinstance(profile, str) else profile
    # About 2.4 people per household (look-alikes included) and 1.8 contacts per person.
    share = profile.get("companies", 0.0)
    households = max(10, round(contacts * (1 - share) / 4.3))
    if profile.get("real"):
        from .real import RealWorld, Sources

        src = Sources.load()
        if src is None:
            raise RuntimeError(
                f"Real sources are not cached: run python -m {__package__}.sources (once; it downloads the public data)"
            )
        world = RealWorld(random.Random(seed), src)
    else:
        world = (CommonNamesWorld if profile.get("common_names") else g.World)(random.Random(seed))
    for hh in range(households):
        world.household(hh)
    for _ in range(profile.get("lookalike_rounds", 1)):
        world.lookalikes()
    if profile.get("real"):
        world.shops(profile.get("shops", 0.0))
        world.firms = world.firms_from_registry(round(contacts * share / 2.2))
    tables, key = make_contacts(world, seed, profile.get("damage", damage), profile)
    data_dir.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)
    columns = {"contacts": CONTACT_COLUMNS, "phones": PHONE_COLUMNS, "addresses": ADDRESS_COLUMNS}
    for name, rows in tables.items():
        g.write_csv(data_dir / FILES[name], columns[name], rows, "utf-8", "\n")
    g.write_csv(
        key_dir / "answer_key_contacts.csv",
        ["system", "record_id", "role", "entity_id", "tags"],
        key,
        "utf-8",
        "\n",
    )
    g.write_csv(
        key_dir / "entities.csv",
        ["entity_id", "kind", "household", "name_kana", "dob", "tags"],
        [
            {
                "entity_id": p["id"],
                "kind": "person",
                "household": p["hh"],
                "name_kana": p["names"][-1][1][1] + p["given"][1],
                "dob": p["dob"].isoformat(),
                "tags": ";".join(p["tags"]),
            }
            for p in world.people
        ]
        + [
            {
                "entity_id": c["id"],
                "kind": "company",
                "household": "",
                "name_kana": c["kana"],
                "dob": c["founded"].isoformat(),
                "tags": "",
            }
            for c in getattr(world, "firms", [])
        ],
        "utf-8",
        "\n",
    )
    persons = len({k["entity_id"] for k in key})
    stats = {
        "contacts": len(key),
        "people": persons,
        "phones": len(tables["phones"]),
        "addresses": len(tables["addresses"]),
        "tags": g.tag_counts(key),
    }
    (key_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    return stats
