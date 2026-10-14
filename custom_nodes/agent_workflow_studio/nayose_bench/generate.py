"""Very dirty synthetic customer data from three legacy Japanese insurance systems (nayose eval).

Stdlib only and deterministic per seed. ``generate(data_dir, key_dir)`` writes what the agent
gets (three exports and the IT handover note) to ``data_dir`` and, separately, the answer key:
which person or company every name mention is, plus tags naming how that mention was damaged.

The damage follows how such data really goes bad: paper forms read by OCR, agency staff who
skip or copy fields, call-centre staff typing names they only heard (wrong kanji, guessed
readings), a mainframe that cannot store some kanji and has no small kana, bank-transfer kana,
maiden names and old addresses updated on some policies but not on others, and municipalities
renamed by mergers. Postal codes, phone and account numbers are synthetic.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import random
import unicodedata
from pathlib import Path

D = dt.date
START, END = D(1988, 1, 1), D(2025, 9, 30)


def rows(text: str) -> list[list[str]]:
    return [item.split() for item in text.replace("\n", "").split("|")]


SURNAMES = rows("""佐藤 サトウ|鈴木 スズキ|高橋 タカハシ|田中 タナカ|伊藤 イトウ|渡辺 ワタナベ|山本 ヤマモト|
中村 ナカムラ|小林 コバヤシ|加藤 カトウ|吉田 ヨシダ|山田 ヤマダ|佐々木 ササキ|山口 ヤマグチ|松本 マツモト|
井上 イノウエ|木村 キムラ|林 ハヤシ|斎藤 サイトウ|清水 シミズ|山崎 ヤマザキ|森 モリ|池田 イケダ|橋本 ハシモト|
阿部 アベ|石川 イシカワ|山下 ヤマシタ|中島 ナカジマ|石井 イシイ|小川 オガワ|前田 マエダ|岡田 オカダ|
長谷川 ハセガワ|藤田 フジタ|後藤 ゴトウ|近藤 コンドウ|村上 ムラカミ|遠藤 エンドウ|青木 アオキ|坂本 サカモト|
斉藤 サイトウ|福田 フクダ|太田 オオタ|西村 ニシムラ|藤井 フジイ|金子 カネコ|岡本 オカモト|藤原 フジワラ|
中野 ナカノ|三浦 ミウラ|原田 ハラダ|中川 ナカガワ|松田 マツダ|竹内 タケウチ|小野 オノ|田村 タムラ|
中山 ナカヤマ|和田 ワダ|石田 イシダ|上田 ウエダ|森田 モリタ|小島 コジマ|柴田 シバタ|原 ハラ|宮崎 ミヤザキ|
酒井 サカイ|工藤 クドウ|横山 ヨコヤマ|宮本 ミヤモト|内田 ウチダ|高木 タカギ|安藤 アンドウ|島田 シマダ|
谷口 タニグチ|大野 オオノ|高田 タカダ|丸山 マルヤマ|今井 イマイ|河野 コウノ|藤本 フジモト|村田 ムラタ|
武田 タケダ|上野 ウエノ|杉山 スギヤマ|増田 マスダ|菅原 スガワラ|平野 ヒラノ|大塚 オオツカ|千葉 チバ|
久保 クボ|松井 マツイ|小山 コヤマ|岩崎 イワサキ|桜井 サクライ|野口 ノグチ|渡部 ワタナベ|菅野 カンノ|
角田 カクタ|吉川 ヨシカワ|東 アズマ|沢田 サワダ|新田 ニッタ|長嶋 ナガシマ""")
# Correct but less common readings: staff who do not ask guess the common one.
ALT_READINGS = {
    "中島": ["ナカシマ"],
    "山崎": ["ヤマサキ"],
    "河野": ["カワノ"],
    "角田": ["ツノダ", "スミダ"],
    "東": ["ヒガシ"],
    "菅野": ["スガノ"],
    "上田": ["カミタ"],
    "小島": ["オジマ"],
    "吉川": ["キッカワ"],
    "渡部": ["ワタベ"],
    "新田": ["シンデン"],
    "宮崎": ["ミヤサキ"],
}
# Registered (koseki) names that use an old or variant character.
TRUE_VARIANTS = {
    "高橋": ["髙橋"],
    "山崎": ["山﨑"],
    "渡辺": ["渡邊", "渡邉"],
    "斎藤": ["齋藤"],
    "斉藤": ["齊藤"],
    "吉田": ["𠮷田"],
    "吉川": ["𠮷川"],
    "宮崎": ["宮﨑"],
    "高木": ["髙木"],
    "高田": ["髙田"],
    "沢田": ["澤田"],
    "桜井": ["櫻井"],
    "島田": ["嶋田"],
}
VARIANT_TO_COMMON = dict(
    zip("髙﨑邊邉齋齊𠮷德澤濱嶋櫻廣國龍惠冨", "高崎辺辺斎斉吉徳沢浜島桜広国竜恵富", strict=True)
)
COMMON_TO_VARIANT = {c: v for v, c in VARIANT_TO_COMMON.items()}
# Characters outside JIS X 0208: the mainframe stores them as the geta mark.
GAIJI = set("髙﨑𠮷德")
SURNAME_HOMOPHONES = [
    ["斎藤", "斉藤", "齋藤", "齊藤"],
    ["渡辺", "渡部", "渡邊", "渡邉"],
    ["太田", "大田"],
    ["伊藤", "伊東"],
    ["小島", "児島", "小嶋"],
    ["中島", "中嶋"],
    ["佐々木", "佐佐木"],
    ["島田", "嶋田"],
    ["沢田", "澤田"],
    ["桜井", "櫻井"],
    ["長嶋", "長島"],
    ["河野", "川野"],
]
GIVEN = [
    r[:3] + [r[3].split(",")]
    for r in rows("""ヒロシ M om 浩,博,弘,寛,宏|タカシ M om 隆,孝,崇,高志|
アキラ M om 明,彰,晃,亮|マコト M m 誠,真,信|ケンイチ M om 健一,賢一,憲一,研一|イチロウ M om 一郎,市郎|
ジュンイチ M m 純一,順一,淳一|シンヤ M m 慎也,伸也,真也,信也|ショウタ M y 翔太,将太,祥太|
ユウスケ M my 雄介,祐介,裕介,勇輔|ヒロト M y 大翔,陽翔,博人|レン M y 蓮,廉|ダイキ M y 大輝,大樹,大貴|
カズオ M o 和夫,一夫,和男|マサオ M o 正雄,正夫,昌男|ススム M o 進,晋|キヨシ M o 清,潔|
シュウヘイ M my 修平,周平|リョウ M my 涼,亮,遼|コウヘイ M my 康平,浩平,航平|ヨウヘイ M m 洋平,陽平|
タロウ M omy 太郎|ジロウ M om 次郎,二郎|ショウゴ M my 翔吾,省吾|ユウキ M y 勇気,祐樹,悠希|
テツヤ M m 哲也,徹也|ヨシオ M o 義雄,良夫,芳男|ユウコ F m 裕子,優子,祐子,有子,夕子|
ケイコ F om 恵子,啓子,敬子,桂子,圭子|ヨウコ F om 洋子,陽子,葉子,容子|キョウコ F om 京子,恭子,響子|
アキコ F om 明子,昭子,晶子|ミチコ F o 美智子,道子,三千子|ノリコ F om 典子,紀子,法子|
ヒロミ F m 裕美,弘美,博美,広美|ジュンコ F om 順子,純子,淳子|サチコ F o 幸子,祥子|カズコ F o 和子,一子|
ハル F o ハル,はる,春|キヨ F o キヨ,きよ|チヒロ F my 千尋,千紘|ユイ F y 結衣,由衣,唯|ヒナ F y 陽菜,日菜|
サクラ F y さくら,桜,咲良|アオイ F y 葵,碧|ミサキ F my 美咲,岬|ユウカ F my 優花,由佳,結花|
マユミ F m 真由美,麻由美|エミ F m 恵美,絵美|ショウコ F om 祥子,翔子,昌子|リョウコ F om 良子,涼子,亮子|
シズカ F my 静香,静|ナツミ F my 夏美,奈津美""")
]
# The long tail: real surname and given-name distributions are flat, so most people are composed
# from common parts (ヤマ+ザキ, ケン+タ); the curated lists above supply the frequent names.
SURNAME_HEADS = rows("""山 ヤマ|中 ナカ|小 コ|大 オオ|高 タカ|石 イシ|松 マツ|藤 フジ|吉 ヨシ|川 カワ|岡 オカ|
森 モリ|木 キ|西 ニシ|北 キタ|宮 ミヤ|長 ナガ|竹 タケ|安 ヤス|福 フク|上 カミ|下 シモ|坂 サカ|平 ヒラ|新 ニイ|
古 フル|今 イマ|菊 キク|杉 スギ|柳 ヤナギ|桑 クワ|梅 ウメ|堀 ホリ|浅 アサ|滝 タキ|玉 タマ|市 イチ|白 シラ|
黒 クロ|赤 アカ|青 アオ|寺 テラ|南 ミナミ|早 ハヤ|秋 アキ|土 ツチ|金 カネ|片 カタ|三 ミ|久 ク""")
SURNAME_TAILS = rows("""田 タ ダ|川 カワ ガワ|山 ヤマ ヤマ|本 モト モト|村 ムラ ムラ|野 ノ ノ|井 イ イ|島 シマ ジマ|
原 ハラ バラ|崎 サキ ザキ|木 キ ギ|口 クチ グチ|沢 サワ ザワ|部 ベ ベ|谷 タニ ダニ|岡 オカ オカ|内 ウチ ウチ|
橋 ハシ バシ|尾 オ オ|瀬 セ ゼ|浦 ウラ ウラ|林 バヤシ バヤシ|森 モリ モリ|池 イケ イケ|塚 ツカ ヅカ|城 シロ ジロ""")
GIVEN_HEADS = {
    "M": rows("""健 ケン|大 ダイ|雄 ユウ|翔 ショウ|拓 タク|直 ナオ|和 カズ|正 マサ|秀 ヒデ|光 ミツ|智 トモ|
克 カツ|達 タツ|俊 トシ|義 ヨシ|幸 ユキ|康 ヤス|信 ノブ|忠 タダ|勝 カツ|輝 テル|隆 タカ|裕 ユウ|悠 ユウ|
昌 マサ|英 ヒデ|敏 トシ|政 マサ|茂 シゲ|篤 アツ"""),
    "F": rows("""和 カズ|幸 サチ|恵 ケイ|由 ユ|真 マ|智 トモ|直 ナオ|明 アキ|文 フミ|春 ハル|千 チ|
佳 ヨシ|愛 アイ|友 トモ|優 ユウ|里 サト|純 ジュン|美 ミ|喜 キ|久 ク|麻 マ|沙 サ|奈 ナ|亜 ア|紗 サ"""),
}
GIVEN_TAILS = {
    "M": rows("""太 タ|一 イチ|二 ジ|郎 ロウ|介 スケ|也 ヤ|樹 キ|人 ト|彦 ヒコ|男 オ|夫 オ|之 ユキ|
平 ヘイ|志 シ|治 ジ|史 シ|司 ジ|斗 ト|輔 スケ|吾 ゴ"""),
    "F": rows(
        """子 コ|美 ミ|香 カ|代 ヨ|江 エ|奈 ナ|絵 エ|乃 ノ|紀 キ|里 リ|菜 ナ|穂 ホ|衣 イ|希 キ"""
    ),
}
# Residents with foreign names: (kanji or None, kana readings, romaji surname, given kana, romaji given).
FOREIGN = [
    ("金", ["キム", "キン"], "KIM", "ミンジュン", "MINJUN"),
    ("李", ["イ", "リ"], "LEE", "ソヨン", "SOYEON"),
    ("王", ["ワン", "オウ"], "WANG", "ウェイ", "WEI"),
    ("張", ["チャン", "チョウ"], "ZHANG", "リン", "LIN"),
    ("朴", ["パク", "ボク"], "PARK", "ジフン", "JIHOON"),
    (None, ["グエン"], "NGUYEN", "ヴァン アン", "VAN AN"),
    (None, ["スミス"], "SMITH", "ジョン", "JOHN"),
    (None, ["シルバ", "シウバ"], "SILVA", "アナ", "ANA"),
    (None, ["サントス"], "SANTOS", "マリア", "MARIA"),
]
# (prefecture, area code, [(from year, locality)], has chome). Histories follow real mergers.
PLACES = [
    ("東京都", "03", [(0, "新宿区西新宿")], True),
    ("東京都", "03", [(0, "世田谷区経堂")], True),
    ("東京都", "03", [(0, "練馬区石神井町")], True),
    ("東京都", "042", [(0, "田無市田無町"), (2001, "西東京市田無町")], True),
    ("東京都", "042", [(0, "保谷市東町"), (2001, "西東京市東町")], True),
    (
        "埼玉県",
        "048",
        [(0, "浦和市高砂"), (2001, "さいたま市高砂"), (2003, "さいたま市浦和区高砂")],
        True,
    ),
    (
        "埼玉県",
        "048",
        [(0, "大宮市桜木町"), (2001, "さいたま市桜木町"), (2003, "さいたま市大宮区桜木町")],
        True,
    ),
    (
        "埼玉県",
        "048",
        [(0, "与野市上落合"), (2001, "さいたま市上落合"), (2003, "さいたま市中央区上落合")],
        True,
    ),
    ("埼玉県", "048", [(0, "川口市栄町")], True),
    (
        "静岡県",
        "054",
        [(0, "清水市江尻町"), (2003, "静岡市江尻町"), (2005, "静岡市清水区江尻町")],
        False,
    ),
    (
        "静岡県",
        "053",
        [(0, "浜松市鍛冶町"), (2007, "浜松市中区鍛冶町"), (2024, "浜松市中央区鍛冶町")],
        False,
    ),
    (
        "静岡県",
        "053",
        [
            (0, "浜北市小松"),
            (2005, "浜松市小松"),
            (2007, "浜松市浜北区小松"),
            (2024, "浜松市浜名区小松"),
        ],
        False,
    ),
    ("長野県", "0263", [(0, "南安曇郡豊科町大字豊科"), (2005, "安曇野市豊科")], False),
    ("長野県", "0263", [(0, "南安曇郡穂高町大字穂高"), (2005, "安曇野市穂高")], False),
    (
        "新潟県",
        "0250",
        [(0, "新津市新津本町"), (2005, "新潟市新津本町"), (2007, "新潟市秋葉区新津本町")],
        True,
    ),
    ("大阪府", "06", [(0, "大阪市北区梅田")], True),
    ("大阪府", "072", [(0, "堺市大浜北町"), (2006, "堺市堺区大浜北町")], True),
    ("京都府", "075", [(0, "京都市中京区二条殿町")], False),
    ("北海道", "011", [(0, "札幌市中央区北一条西")], True),
    ("宮城県", "022", [(0, "仙台市青葉区一番町")], True),
    ("愛知県", "052", [(0, "名古屋市中区栄")], True),
    ("神奈川県", "045", [(0, "横浜市港北区日吉")], True),
    ("神奈川県", "044", [(0, "川崎市中原区小杉町")], True),
    ("千葉県", "047", [(0, "船橋市本町")], True),
    ("兵庫県", "078", [(0, "神戸市中央区三宮町")], True),
    ("広島県", "082", [(0, "広島市中区紙屋町")], True),
    ("福岡県", "092", [(0, "福岡市博多区博多駅前")], True),
]
BUILDINGS = [
    "コーポ山田",
    "サンハイツ",
    "メゾン桜",
    "グランドール",
    "パークハイム",
    "ハイツ松風",
    "レジデンス栄",
]
COMPANY_PARTS = [
    ("山田", "ヤマダ"),
    ("丸八", "マルハチ"),
    ("東和", "トウワ"),
    ("さくら", "サクラ"),
    ("中央", "チュウオウ"),
    ("大和", "ダイワ"),
    ("富士", "フジ"),
    ("日進", "ニッシン"),
    ("三和", "サンワ"),
    ("共栄", "キョウエイ"),
]
COMPANY_TRADES = [
    ("商事", "ショウジ"),
    ("運送", "ウンソウ"),
    ("建設", "ケンセツ"),
    ("不動産", "フドウサン"),
    ("物産", "ブッサン"),
    ("製作所", "セイサクショ"),
    ("工業", "コウギョウ"),
    ("食品", "ショクヒン"),
]
BANKS = ["0001", "0005", "0009", "0010", "0017", "0033", "0036", "0150", "9900"]
PRODUCTS = {
    "01": ("自動車", "mf", "A"),
    "02": ("火災", "mf", "F"),
    "03": ("医療", "med", "M"),
    "04": ("傷害", "med", "S"),
    "05": ("個人賠償", "web", "L"),
}
AGENCY_SLOPPINESS = [0.4, 0.6, 0.8, 1.0, 1.0, 1.2, 1.5, 2.0, 2.8]

# --- text damage ---------------------------------------------------------------------------
FULL_KANA = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンァィゥェォッャュョー・　"
HALF_KANA = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜｦﾝｧｨｩｪｫｯｬｭｮｰ･ "
TO_HALF = dict(zip(FULL_KANA, HALF_KANA, strict=True)) | {"゙": "ﾞ", "゚": "ﾟ"}
NO_SMALL = str.maketrans("ァィゥェォッャュョヮ", "アイウエオツヤユヨワ")
WIDE = str.maketrans("0123456789-", "０１２３４５６７８９－")
LONG_VOWELS = [
    "オウ",
    "コウ",
    "ソウ",
    "トウ",
    "ノウ",
    "ホウ",
    "モウ",
    "ヨウ",
    "ロウ",
    "ゴウ",
    "ゾウ",
    "ドウ",
    "ボウ",
    "ョウ",
    "ユウ",
    "ュウ",
    "オオ",
    "クウ",
    "ツウ",
]
OCR = dict(
    zip(
        "ソンシツクタウロニカエハートペパピプポヘ未末土士己日人入大太王千田木本崎島祐一二口0158",
        "ンソツシタ夕ワ口二力工八一卜ベバビブボへ末未士土已曰入人太大玉干由本木埼鳥裕ーニロOlSB",
        strict=True,
    )
)


def half(s: str) -> str:
    return "".join(TO_HALF.get(ch, ch) for ch in unicodedata.normalize("NFD", s))


def hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def zengin(s: str) -> str:
    """Bank-transfer kana: half-width, upper case, no small kana, a short fixed field."""
    s = half(s.translate(NO_SMALL)).replace("ｰ", "-").replace("･", ".").upper()
    return "".join(
        c for c in s if "ｦ" <= c <= "ﾟ" or c.isascii() and (c.isalnum() or c in " ()-./")
    )[:30]


def kansuji(n: int) -> str:
    digits = "〇一二三四五六七八九"
    if n < 10:
        return digits[n]
    tens, ones = divmod(n, 10)
    return (
        ("" if tens == 1 else digits[tens]) + "十" + (digits[ones] if ones else "")
        if n < 100
        else str(n)
    )


class Damage:
    """Applies one record's damage with probabilities scaled by how sloppy its source is."""

    def __init__(self, rng: random.Random, sloppiness: float, channel: str, scale: float = 1.0):
        self.rng, self.s, self.channel, self.tags = rng, sloppiness * scale, channel, set()
        self.scale = scale

    def hit(self, base: float, tag: str | None = None) -> bool:
        if self.rng.random() < min(0.9, base * self.s):
            if tag:
                self.tags.add(tag)
            return True
        return False

    def ocr(self, s: str, rate: float = 0.35) -> str:
        if self.channel != "paper" or not s or self.rng.random() > rate * self.scale:
            return s
        chars = list(s)
        spots = [i for i, c in enumerate(chars) if c in OCR or unicodedata.normalize("NFD", c) != c]
        for i in self.rng.sample(spots, min(len(spots), self.rng.choice([1, 1, 2]))):
            undotted = unicodedata.normalize("NFD", chars[i])[0]  # a lost dakuten
            new = (OCR.get(chars[i]) or undotted) if self.rng.random() < 0.7 else undotted
            if new != chars[i]:
                chars[i] = new
                self.tags.add("ocr")
        return "".join(chars)

    def digit_typo(self, s: str) -> str:
        spots = [i for i, c in enumerate(s) if c.isdigit()]
        if not spots:
            return s
        i = self.rng.choice(spots)
        return s[:i] + str((int(s[i]) + self.rng.choice([1, 9])) % 10) + s[i + 1 :]

    def typo(self, s: str, base: float) -> str:
        if len(s) < 3 or not self.hit(base, "typo"):
            return s
        i = self.rng.randrange(1, len(s) - 1)
        kind = self.rng.random()
        if kind < 0.35:
            return s[:i] + s[i + 1 :]
        if kind < 0.6:
            return s[:i] + s[i] + s[i:]
        if kind < 0.85:
            return s[: i - 1] + s[i] + s[i - 1] + s[i + 1 :]
        return self.digit_typo(s) if s[i].isdigit() else s[:i] + s[i + 1 :]


# --- people, households, companies ---------------------------------------------------------
def rand_date(rng: random.Random, a: D, b: D) -> D:
    return a + dt.timedelta(days=rng.randint(0, max(0, (b - a).days)))


def plus_years(d: D, years: int) -> D:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def at(events: list, d: D):
    value = events[0][1]
    for when, v in events:
        if when > d:
            break
        value = v
    return value


class World:
    def __init__(self, rng: random.Random):
        self.rng, self.people, self.companies, self.numbers = rng, [], [], set()
        # Real shares: 佐藤 ~1.5%, the top 100 surnames ~30%; a flatter curve keeps homonyms realistic.
        self.surname_weights = [1 / (rank + 1) ** 0.6 for rank in range(len(SURNAMES))]

    def by_id(self) -> dict:
        return {e["id"]: e for e in self.people + self.companies}

    def unique(self, make) -> str:
        while (value := make()) in self.numbers:
            pass
        self.numbers.add(value)
        return value

    def surname(self) -> tuple:
        if self.rng.random() < 0.6:
            (h, hk), (t, plain, voiced) = (
                self.rng.choice(SURNAME_HEADS),
                self.rng.choice(SURNAME_TAILS),
            )
            if h == t:
                return self.surname()
            reading = h + t, hk + (voiced if self.rng.random() < 0.5 else plain)
            return (*reading, hk + plain)  # a clerk guessing reads the unvoiced form
        kanji, kana = self.rng.choices(SURNAMES, self.surname_weights)[0]
        guess = kana
        if kanji in ALT_READINGS and self.rng.random() < 0.3:
            kana = self.rng.choice(ALT_READINGS[kanji])
        if kanji in TRUE_VARIANTS and self.rng.random() < 0.15:
            kanji = self.rng.choice(TRUE_VARIANTS[kanji])
        return (kanji, kana, guess)

    def given(self, sex: str, born: D) -> tuple:
        if self.rng.random() < 0.65:
            (h, hk), (t, tk) = self.rng.choice(GIVEN_HEADS[sex]), self.rng.choice(GIVEN_TAILS[sex])
            return (h + t, hk + tk)
        era = "o" if born.year < 1955 else "m" if born.year < 1985 else "y"
        kana, _, _, kanji = self.rng.choice([g for g in GIVEN if g[1] == sex and era in g[2]])
        return (self.rng.choices(kanji, [3] + [1] * (len(kanji) - 1))[0], kana)

    def address(self, place: int | None, since: D) -> tuple:
        rng = self.rng
        place = rng.randrange(len(PLACES)) if place is None else place
        pref, area, history, chome = PLACES[place]
        numbers = (
            (rng.randint(1, 5), rng.randint(1, 30), rng.randint(1, 20))
            if chome
            else (rng.randint(1, 2999),)
        )
        building = (
            f"{rng.choice(BUILDINGS)}{rng.randint(1, 9)}0{rng.randint(1, 9)}"
            if rng.random() < 0.4
            else ""
        )
        landline = ""
        if rng.random() < (0.7 if since.year < 2005 else 0.3):
            exchange = 10 - len(area) - 4
            landline = self.unique(
                lambda: (
                    f"{area}-{rng.randint(10 ** (exchange - 1) * 2, 10**exchange - 1)}-{rng.randint(0, 9999):04d}"
                )
            )
        return (place, numbers, building, landline)

    def person(self, hh: int, sex: str, born: D, surname: tuple, home: list, foreign=None) -> dict:
        rng = self.rng
        p = {
            "id": f"P{len(self.people) + 1:05d}",
            "kind": "person",
            "hh": hh,
            "sex": sex,
            "dob": born,
            "given": self.given(sex, born),
            "names": [(born, surname)],
            "addr": list(home),
            "mobile": [(born, "")],
            "accounts": [],
            "foreign": foreign,
            "spouse": None,
            "children": [],
            "parents": [],
            "tags": [],
        }
        adult = plus_years(born, 18)
        if adult < END and rng.random() < (0.6 if born.year < 1945 else 0.92):
            got = rand_date(rng, max(adult, D(1999, 1, 1)), END)
            p["mobile"].append(
                (
                    got,
                    self.unique(
                        lambda: (
                            f"0{rng.choice('987')}0-{rng.randint(1000, 9999)}-{rng.randint(0, 9999):04d}"
                        )
                    ),
                )
            )
            if rng.random() < 0.2:
                p["mobile"].append(
                    (
                        rand_date(rng, got, END),
                        self.unique(
                            lambda: (
                                f"0{rng.choice('987')}0-{rng.randint(1000, 9999)}-{rng.randint(0, 9999):04d}"
                            )
                        ),
                    )
                )
        for _ in range(1 if rng.random() < 0.75 else 2):
            opened = rand_date(rng, max(adult, D(1980, 1, 1)), END)
            if opened < END:
                number = self.unique(
                    lambda: (
                        rng.choice(BANKS),
                        f"{rng.randint(1, 999):03d}",
                        f"{rng.randint(1, 9999999):07d}",
                    )
                )
                p["accounts"].append(
                    {"number": number, "opened": opened, "renamed": rng.random() < 0.55}
                )
        self.people.append(p)
        return p

    def move(self, people: list, when: D, address: tuple) -> None:
        for p in people:
            p["addr"] = [e for e in p["addr"] if e[0] < when] + [(when, address)]

    def household(self, hh: int) -> None:
        rng = self.rng
        kind = rng.choices(["single", "couple", "family", "elder"], [28, 22, 38, 12])[0]
        foreign = rng.choice(FOREIGN) if rng.random() < 0.025 else None
        name = (
            (foreign[0] or foreign[1][0], foreign[1][0], foreign[1][0])
            if foreign
            else self.surname()
        )
        if kind == "elder":
            parent_sex = rng.choice("MF")
            parent = self.person(
                hh,
                parent_sex,
                rand_date(rng, D(1930, 1, 1), D(1950, 12, 31)),
                name,
                [(D(1900, 1, 1), self.address(None, START))],
                foreign,
            )
            child = self.person(
                hh,
                rng.choice("MF"),
                rand_date(rng, plus_years(parent["dob"], 22), plus_years(parent["dob"], 38)),
                name,
                parent["addr"],
                foreign,
            )
            child["parents"].append(parent["id"])
            parent["children"].append(child["id"])
            self.life_moves([parent, child], START)
            return
        head_sex = rng.choice("MF") if kind == "single" else ("M" if rng.random() < 0.9 else "F")
        born = rand_date(
            rng, D(1938, 1, 1), D(2003, 12, 31) if kind == "single" else D(1995, 12, 31)
        )
        head = self.person(
            hh, head_sex, born, name, [(D(1900, 1, 1), self.address(None, START))], foreign
        )
        if kind == "single":
            self.life_moves([head], START)
            return
        married = rand_date(rng, plus_years(born, 23), plus_years(born, 36))
        if married > END:
            self.life_moves([head], START)
            return
        maiden = self.surname()
        partner = self.person(
            hh,
            "F" if head_sex == "M" else "M",
            rand_date(rng, plus_years(born, -6), plus_years(born, 4)),
            maiden,
            [(D(1900, 1, 1), self.address(None, START))],
        )
        if rng.random() < 0.04:  # the husband takes the wife's name
            head, partner = partner, head
        partner["names"].append((married, head["names"][0][1]))
        head["spouse"], partner["spouse"] = partner["id"], head["id"]
        home = (
            head["addr"][-1][1]
            if rng.random() < 0.5
            else self.address(head["addr"][-1][1][0], married)
        )
        self.move([head, partner], married, home)
        members = [head, partner]
        if kind == "family":
            for i in range(rng.choice([1, 1, 2, 2, 3])):
                kid_born = rand_date(rng, plus_years(married, 1), plus_years(married, 12))
                if i and rng.random() < 0.04:
                    kid_born = members[-1]["dob"]  # twins
                if kid_born >= END:
                    break
                kid = self.person(
                    hh,
                    rng.choice("MF"),
                    kid_born,
                    head["names"][0][1],
                    [(D(1900, 1, 1), at(head["addr"], kid_born))],
                )
                if kid["sex"] == head["sex"] == "M" and rng.random() < 0.02:
                    kid["given"] = head["given"]  # named after the father
                    kid["tags"].append("same_name_as_parent")
                kid["parents"] = [head["id"], partner["id"]]
                head["children"].append(kid["id"])
                partner["children"].append(kid["id"])
                members.append(kid)
        self.life_moves(members, married)
        for kid in members[2:]:
            leave = rand_date(rng, plus_years(kid["dob"], 22), plus_years(kid["dob"], 30))
            if leave < END and rng.random() < 0.6:
                self.move(
                    [kid],
                    leave,
                    self.address(None if rng.random() < 0.5 else kid["addr"][-1][1][0], leave),
                )
                wed = rand_date(rng, plus_years(kid["dob"], 25), plus_years(kid["dob"], 36))
                if kid["sex"] == "F" and wed < END and rng.random() < 0.5:
                    kid["names"].append((wed, self.surname()))
                    self.move([kid], wed, self.address(None, wed))

    def life_moves(self, members: list, since: D) -> None:
        when = max(since, START)
        for _ in range(self.rng.choice([0, 0, 1, 1, 2, 3])):
            when = rand_date(self.rng, when, END)
            if when >= END:
                break
            same_place = self.rng.random() < 0.6
            self.move(
                members,
                when,
                self.address(members[0]["addr"][-1][1][0] if same_place else None, when),
            )

    def lookalikes(self) -> None:
        """Strangers with exactly the same name (and sometimes birth date) as an existing customer."""
        rng = self.rng
        for p in rng.sample(self.people, len(self.people) // 50):
            if p["foreign"] or plus_years(p["dob"], 20) > END:
                continue
            born = (
                p["dob"]
                if rng.random() < 0.35
                else rand_date(rng, plus_years(p["dob"], -8), plus_years(p["dob"], 8))
            )
            twin = self.person(
                10_000_000 + len(self.people),
                p["sex"],
                born,
                p["names"][-1][1],
                [(D(1900, 1, 1), self.address(None, START))],
            )
            twin["given"], twin["tags"] = p["given"], ["lookalike"]
            p["tags"].append("lookalike")
            self.life_moves([twin], START)

    def company(self) -> dict:
        rng = self.rng
        part, trade = rng.choice(COMPANY_PARTS), rng.choice(COMPANY_TRADES)
        legal = rng.choice(["株式会社", "株式会社", "株式会社", "有限会社"])
        c = {
            "id": f"C{len(self.companies) + 1:04d}",
            "kind": "company",
            "core": (part[0] + trade[0], part[1] + trade[1]),
            "legal": legal,
            "front": rng.random() < 0.6,
            "founded": rand_date(rng, D(1960, 1, 1), D(2015, 1, 1)),
        }
        c["addr"] = [(D(1900, 1, 1), self.address(None, START))]
        c["accounts"] = [
            {
                "number": self.unique(
                    lambda: (
                        rng.choice(BANKS),
                        f"{rng.randint(1, 999):03d}",
                        f"{rng.randint(1, 9999999):07d}",
                    )
                ),
                "opened": c["founded"],
                "renamed": True,
            }
        ]
        if rng.random() < 0.3:
            when = rand_date(rng, max(START, c["founded"]), END)
            c["addr"].append((when, self.address(None, when)))
        self.companies.append(c)
        return c


# --- rendering one mention --------------------------------------------------------------------
def render_date(dmg: Damage, d: D | None, style: str) -> str:
    rng = dmg.rng
    if d is None or dmg.hit(0.04, "dob_missing"):
        return ""
    if dmg.hit(0.008, "dob_placeholder"):
        return {"wa_code": "0000000", "wa_letter": "S00.00.00", "wa_kanji": "不明"}.get(
            style, "1900/01/01"
        )
    if d.day <= 12 and d.day != d.month and dmg.hit(0.015, "dob_swapped"):
        d = D(d.year, d.day, d.month)
    if style.startswith("wa"):
        eras = [
            (D(2019, 5, 1), "令和", "R", 5),
            (D(1989, 1, 8), "平成", "H", 4),
            (D(1926, 12, 25), "昭和", "S", 3),
        ]
        start, name, letter, code = next(era for era in eras if d >= era[0])
        year = d.year - start.year + 1
        if dmg.hit(0.015, "dob_era"):
            letter, name, code = {
                "S": ("H", "平成", 4),
                "H": ("S", "昭和", 3),
                "R": ("H", "平成", 4),
            }[letter]
        text = {
            "wa_letter": f"{letter}{year:02d}.{d.month:02d}.{d.day:02d}",
            "wa_kanji": f"{name}{'元' if year == 1 else year}年{d.month}月{d.day}日",
            "wa_code": f"{code}{year:02d}{d.month:02d}{d.day:02d}",
        }[style]
    else:
        text = rng.choice(
            [
                f"{d.year}/{d.month:02d}/{d.day:02d}",
                f"{d.year}-{d.month}-{d.day}",
                f"{d.year}{d.month:02d}{d.day:02d}",
                f"{d.year}年{d.month}月{d.day}日",
            ]
        )
        if dmg.hit(0.02, "dob_two_digit_year"):
            text = f"{d.year % 100:02d}/{d.month:02d}/{d.day:02d}"
    if dmg.hit(0.015, "dob_typo"):
        text = dmg.digit_typo(text)
    return dmg.ocr(text, 0.2)


def render_address(dmg: Damage, address: tuple, when: D, max_len: int = 0) -> tuple[str, str]:
    rng = dmg.rng
    place, numbers, building, _ = address
    pref, _, history, _ = PLACES[place]
    locality = at([(D(max(y, 1), 1, 1), loc) for y, loc in history], when)
    if len(history) > 1 and dmg.hit(0.12):  # migrated without updating the municipality
        locality = history[0][1]
    if locality != history[-1][1]:
        dmg.tags.add("old_city_name")
    if "中京区" in locality and rng.random() < 0.3:  # Kyoto street-style address
        locality = locality.replace("中京区", "中京区烏丸通御池上る")
    style = rng.random()
    if len(numbers) == 3:
        c, b, g = numbers
        number = (
            f"{c}-{b}-{g}"
            if style < 0.45
            else f"{c}丁目{b}番{g}号"
            if style < 0.7
            else f"{kansuji(c)}丁目{kansuji(b)}番{kansuji(g)}号"
            if style < 0.82
            else f"{c}丁目{b}-{g}"
            if style < 0.93
            else f"{c}ー{b}ー{g}"
        )
        if dmg.hit(0.04, "address_partial"):
            number = f"{c}丁目" if rng.random() < 0.5 else ""
    else:
        number = f"{numbers[0]}番地" if style < 0.6 else str(numbers[0])
    if rng.random() < 0.25:
        number = number.translate(WIDE)
    room = building if building and rng.random() < 0.6 else ""
    if room and rng.random() < 0.3:
        room = room[:-3] + " " + room[-3:] + "号室"
    sep = rng.choice(["", "", " ", "　"])
    text = (
        ("" if dmg.hit(0.25) else pref)
        + locality
        + sep
        + number
        + (rng.choice(["", " ", "　"]) + room if room else "")
    )
    text = dmg.typo(dmg.ocr(text, 0.3), 0.02)
    if max_len and len(text) > max_len:
        text = text[:max_len]
        dmg.tags.add("truncated")
    new = int(hashlib.md5(history[-1][1].encode()).hexdigest()[:7], 16) % 9_000_000 + 1_000_000
    zip_code = f"{new // 10000:03d}-{new % 10000:04d}"
    if when < D(1998, 2, 2):
        zip_code = zip_code[:3] if rng.random() < 0.5 else zip_code[:6]
    elif rng.random() < 0.3:
        zip_code = zip_code.replace("-", "")
    return text, "" if dmg.hit(0.08) else zip_code


def render_phone(dmg: Damage, number: str) -> str:
    if not number or dmg.hit(0.05):
        return ""
    if dmg.hit(0.01, "phone_placeholder"):
        return "000-0000-0000"
    style = dmg.rng.random()
    text = (
        number
        if style < 0.45
        else number.replace("-", "")
        if style < 0.75
        else number.replace("-", "(", 1).replace("-", ")", 1)
        if style < 0.85
        else number.translate(WIDE)
    )
    if dmg.hit(0.02, "phone_lost_zero"):
        text = text.lstrip("0０(")
    return dmg.ocr(dmg.typo(text, 0.02), 0.2)


def name_parts(dmg: Damage, entity: dict, when: D) -> tuple[str, str, str, str]:
    """(kanji surname, kanji given, kana surname, kana given) as this record would hold them."""
    rng = dmg.rng
    surname = at(entity["names"], when)
    if surname != entity["names"][-1][1]:
        dmg.tags.add("old_surname")
    kanji_sur, kana_sur, guess = surname
    kanji_given, kana_given = entity["given"]
    if entity["foreign"]:
        dmg.tags.add("foreign")
        kanji, kanas, roman, given_kana, given_roman = entity["foreign"]
        kana_sur = rng.choice(kanas)
        if kanji is None or rng.random() < 0.3:
            kanji_sur, kanji_given = (
                (roman, given_roman) if rng.random() < 0.3 else (kana_sur, given_kana)
            )
        else:
            kanji_given = given_kana
        return (
            kanji_sur,
            kanji_given,
            kana_sur,
            given_kana.replace(" ", rng.choice(["", " ", "・"])),
        )
    if guess != kana_sur and dmg.channel != "web" and rng.random() < 0.5:
        kana_sur = guess
        dmg.tags.add("reading_guess")
    return kanji_sur, kanji_given, kana_sur, kana_given


def render_name(dmg: Damage, entity: dict, when: D, system: str) -> tuple[str, str]:
    """(kanji name field, kana name field)."""
    rng = dmg.rng
    if entity["kind"] == "company":
        return render_company(dmg, entity, system)
    kanji_sur, kanji_given, kana_sur, kana_given = name_parts(dmg, entity, when)
    if any(VARIANT_TO_COMMON.get(c) for c in kanji_sur) and rng.random() < (
        0.6 if system == "claims" else 0.35
    ):
        kanji_sur = "".join(VARIANT_TO_COMMON.get(c, c) for c in kanji_sur)
        dmg.tags.add("variant_normalized")
    if dmg.hit(0.03, "itaiji"):
        kanji_sur = "".join(COMMON_TO_VARIANT.get(c, c) for c in kanji_sur)
    if dmg.hit(0.2 if dmg.channel == "phone" else 0.02):  # the wrong kanji for a name only heard
        group = [
            g for g in next((g for g in SURNAME_HOMOPHONES if kanji_sur in g), []) if g != kanji_sur
        ]
        options = [
            o for o in next((g[3] for g in GIVEN if g[0] == kana_given), []) if o != kanji_given
        ]
        if group and (rng.random() < 0.4 or not options):
            kanji_sur = rng.choice(group)
            dmg.tags.add("homophone")
        elif options:
            kanji_given = rng.choice(options)
            dmg.tags.add("homophone")
    sep = rng.choice(["", " ", "　", "　"])
    kanji = kanji_sur + sep + kanji_given
    if dmg.hit(0.12 if dmg.channel == "phone" else 0.02, "kana_only"):
        kanji = (hira if rng.random() < 0.4 else str)(kana_sur + sep + kana_given)
    if dmg.hit(0.015, "given_missing"):
        kanji = kanji_sur
    kanji = dmg.typo(dmg.ocr(kanji), 0.015)
    kana = kana_sur + rng.choice(["", " ", "　"]) + kana_given
    if dmg.hit(0.08 if dmg.channel in ("web", "phone") else 0.03, "long_vowel"):
        options = [lv for lv in LONG_VOWELS if lv in kana]
        if options:
            choice = rng.choice(options)
            kana = kana.replace(choice, choice[0] + "ー", 1)
    for a, b in (("ヅ", "ズ"), ("ヂ", "ジ")):  # same sound, two spellings
        if a in kana and dmg.hit(0.3, "kana_variant"):
            kana = kana.replace(a, b)
    if dmg.channel == "web" and rng.random() < 0.3:
        kana = hira(kana)
    kana = dmg.typo(dmg.ocr(kana), 0.02)
    if system == "mf":  # the mainframe: no small kana, half-width, fixed fields, no gaiji
        if kana.translate(NO_SMALL) != kana:
            dmg.tags.add("no_small_kana")
        kana = half(kana.translate(NO_SMALL))
        if len(kana) > 20:
            kana, _ = kana[:20], dmg.tags.add("truncated")
        if len(kanji) > 10:
            kanji, _ = kanji[:10], dmg.tags.add("truncated")
        if any(c in GAIJI for c in kanji):
            kanji = "".join("〓" if c in GAIJI else c for c in kanji)
            dmg.tags.add("gaiji")
    if dmg.hit(0.025, "kanji_missing"):
        kanji = ""
    if kanji and dmg.hit(0.04, "kana_missing"):
        kana = ""
    return kanji, kana


def render_company(dmg: Damage, c: dict, system: str) -> tuple[str, str]:
    rng = dmg.rng
    dmg.tags.add("company")
    core, core_kana = c["core"]
    legal = c["legal"]
    short = {"株式会社": "(株)", "有限会社": "(有)"}[legal]
    form = rng.choice([legal, legal, short, short, {"株式会社": "㈱", "有限会社": "㈲"}[legal], ""])
    kanji = form + core if c["front"] else core + form
    legal_kana = {"株式会社": "カブシキガイシャ", "有限会社": "ユウゲンガイシャ"}[legal]
    kana = rng.choice(
        [core_kana, core_kana, legal_kana + core_kana if c["front"] else core_kana + legal_kana]
    )
    if system == "mf":
        kana = half(kana.translate(NO_SMALL))
    return dmg.ocr(kanji), kana


def zengin_name(dmg: Damage, entity: dict, account: dict, when: D) -> str:
    dmg.tags.add("zengin")
    if entity["kind"] == "company":
        dmg.tags.add("company")
        mark = {"株式会社": "ｶ", "有限会社": "ﾕ"}[entity["legal"]]
        core = zengin(entity["core"][1])
        return f"{mark}){core}" if entity["front"] else f"{core}({mark}"
    name_on_account = when if account["renamed"] else account["opened"]
    _, _, kana_sur, kana_given = name_parts(dmg, entity, name_on_account)
    if entity["names"][-1][1][1] != at(entity["names"], name_on_account)[1]:
        dmg.tags.add("old_surname")
    return dmg.typo(zengin(f"{kana_sur} {kana_given}"), 0.01)


# --- stage 1: people ---------------------------------------------------------------------------
def population(households: int = 1200, seed: int = 7) -> World:
    """Households with life histories (moves, marriages, children), look-alikes and companies."""
    world = World(random.Random(seed))
    for hh in range(households):
        world.household(hh)
    world.lookalikes()
    for _ in range(max(2, households // 30)):
        world.company()
    return world


# --- stage 2: insurance activity: clean facts, nothing damaged yet -----------------------------
def activity(world: World, seed: int = 7, update_rate: float = 0.5) -> dict:
    """Policies, claims and payment registrations: who, what, when, through which channel.

    ``update_rate`` is how often a customer's change (move, marriage, new phone) is reported to
    a policy at all; each policy then keeps or misses it separately.
    """
    rng = random.Random(f"activity-{seed}")
    by_id = world.by_id()
    agencies = {f"AG{i:03d}": rng.choice(AGENCY_SLOPPINESS) for i in range(1, 41)}
    policies, claims, payments = [], [], []

    def adult(pid, when):
        p = by_id.get(pid)
        return p if p and plus_years(p["dob"], 18) <= when else None

    def pay(kanri: str, kind: str, entity: dict, when: D) -> None:
        accounts = entity["accounts"]
        if not accounts:
            return
        index = 0 if len(accounts) == 1 or rng.random() < 0.7 else len(accounts) - 1
        when = min(END, max(when, accounts[index]["opened"]))
        payments.append(
            {
                "rec": f"{len(payments) + 1:07d}",
                "kanri": kanri,
                "kind": kind,
                "entity": entity["id"],
                "account": index,
                "when": when,
            }
        )

    holders = [p for p in world.people if plus_years(p["dob"], 20) < END]
    plan = [
        (p, n) for p in holders for n in range(rng.choices([0, 1, 2, 3, 4], [25, 35, 22, 12, 6])[0])
    ]
    plan += [(c, n) for c in world.companies for n in range(rng.randint(1, 4))]
    rng.shuffle(plan)
    for holder, _ in plan:
        person = holder["kind"] == "person"
        code = rng.choice(["01", "02", "03", "04", "05"] if person else ["01", "01", "02"])
        _, sub, letter = PRODUCTS[code]
        earliest = max(START, plus_years(holder["dob"], 20) if person else holder["founded"])
        signed = rand_date(rng, earliest, END - dt.timedelta(days=30))
        ended = rand_date(rng, signed, END) if rng.random() < 0.3 else None
        frozen = ended or END
        channel = (
            "paper"
            if signed.year < 2005 and rng.random() < 0.7
            else rng.choices(
                ["agency", "web", "paper"], [60, 25 if signed.year >= 2008 else 0, 15]
            )[0]
        )
        if code == "05" and channel == "paper":
            channel = "web"
        agency = rng.choice(list(agencies)) if channel != "web" else ""
        sloppiness = {"paper": 1.3, "web": 0.6}.get(channel) or agencies[agency]
        events = sorted(
            {
                d
                for d, _ in holder["addr"][1:]
                + holder.get("names", [])[1:]
                + holder.get("mobile", [])[1:]
                if signed < d <= frozen
            }
        )
        reported = [d for d in events if rng.random() < update_rate]
        # Each field group shows the state as of its own last reported update.
        as_of = {
            group: max([signed] + [d for d in reported if rng.random() < keep])
            for group, keep in (("name", 0.8), ("addr", 0.9), ("tel", 0.6))
        }
        policy = {
            "rec": f"{len(policies) + 1:07d}",
            "number": f"{letter}{rng.randint(10**8, 10**9 - 1)}",
            "code": code,
            "sub": sub,
            "holder": holder["id"],
            "signed": signed,
            "ended": ended,
            "channel": channel,
            "agency": agency,
            "sloppiness": sloppiness,
            "as_of": as_of,
            "date_style": {
                "mf": rng.choice(["wa_code", "wa_letter"]),
                "med": rng.choice(["wa_letter", "wa_kanji", "west"]),
                "web": "west",
            }[sub],
            "insured": None,
            "insured_blank": False,
            "insured_when": signed,
            "beneficiary": None,
            "placeholder": "",
        }
        insured = None
        if person and code in ("01", "03", "04"):
            options = [holder] * 6
            spouse = by_id.get(holder["spouse"])
            if code != "01":  # auto insures the holder; medical and injury may cover the family
                if spouse and at(spouse["names"], signed) == at(
                    holder["names"], signed
                ):  # married by then
                    options += [spouse] * 3
                options += [by_id[k] for k in holder["children"] if by_id[k]["dob"] < signed]
            insured = rng.choice(options)
            policy["insured"] = insured["id"]
            policy["insured_blank"] = insured is holder and rng.random() < 0.5 * sloppiness
            # Insured details are rarely updated after signing.
            policy["insured_when"] = signed if rng.random() < 0.7 else as_of["addr"]
        if person and code in ("03", "04"):
            kin = [
                by_id[k]
                for k in [holder["spouse"], *holder["children"], *holder["parents"]]
                if k and adult(k, signed)
            ]
            if kin and rng.random() < 0.65:
                policy["beneficiary"] = rng.choice(kin)["id"]
            else:
                policy["placeholder"] = rng.choice(["法定相続人", "法定相続人", "未指定", ""])
        policies.append(policy)
        payer = holder  # premiums: the holder's account, or a spouse's or parent's
        if person:
            others = [adult(k, signed) for k in [holder["spouse"], *holder["parents"]]]
            others = [
                o for o in others if o and o["accounts"] and o["accounts"][0]["opened"] <= signed
            ]
            if others and rng.random() < 0.3:
                payer = rng.choice(others)
        pay(policy["number"], "1", payer, signed)
        for _ in range(rng.choices([0, 1, 2, 3], [62, 26, 9, 3])[0]):
            happened = rand_date(rng, signed, frozen)
            received = min(END, happened + dt.timedelta(days=rng.randint(0, 40)))
            claimant = holder
            if person:
                kin = [
                    k
                    for k in (adult(k, received) for k in [holder["spouse"], *holder["children"]])
                    if k
                ]
                if kin and rng.random() < 0.35:
                    claimant = rng.choice(kin)
            claim = {
                "rec": f"{len(claims) + 1:07d}",
                "uketsuke": f"{received.year}-{rng.randint(100000, 999999)}",
                "policy": policy["rec"],
                "holder": holder["id"],
                "happened": happened,
                "received": received,
                "channel": rng.choices(
                    ["phone", "paper", "web"], [60, 25, 15 if received.year >= 2010 else 0]
                )[0],
                "claimant": claimant["id"],
                "insured": insured["id"]
                if insured is not None and (code != "01" or rng.random() < 0.5)
                else None,
            }
            claims.append(claim)
            payee = claimant if claimant["accounts"] and rng.random() < 0.9 else payer
            pay(claim["uketsuke"], "2", payee, received)
    return {"world": world, "policies": policies, "claims": claims, "payments": payments}


# --- stage 3: legacy exports: each system damages the same facts in its own way ----------------
def role_fields(
    dmg: Damage,
    prefix: str,
    entity: dict,
    name_when: D,
    addr_when: D,
    sub: str,
    date_style: str,
    parts: str = "name birth sex addr",
    holder_addr=None,
):
    """One role's columns; name and address may be as of different dates (updated separately)."""
    kanji, kana = render_name(dmg, entity, name_when, sub)
    fields = {f"{prefix}_NM_KJ": kanji, f"{prefix}_NM_KN": kana}
    if entity["kind"] == "person" and "birth" in parts:
        fields[f"{prefix}_BIRTH"] = render_date(dmg, entity["dob"], date_style)
    if entity["kind"] == "person" and "sex" in parts:
        sex = (
            entity["sex"] if not dmg.hit(0.01, "sex_wrong") else {"M": "F", "F": "M"}[entity["sex"]]
        )
        fields[f"{prefix}_SEX"] = "" if dmg.hit(0.05) else {"M": "1", "F": "2"}[sex]
    address = at(entity["addr"], addr_when)
    if "addr" not in parts:
        return fields, address
    if address != entity["addr"][-1][1]:
        dmg.tags.add("stale_address")
    if holder_addr is not None and address == holder_addr and dmg.rng.random() < 0.5 * dmg.s:
        fields[f"{prefix}_ADDR"], fields[f"{prefix}_ZIP"] = "同上", ""
        dmg.tags.add("same_as_above")
    else:
        fields[f"{prefix}_ADDR"], fields[f"{prefix}_ZIP"] = render_address(
            dmg, address, addr_when, 40 if sub == "mf" else 0
        )
    return fields, address


SYSTEMS = {"policies": "policy_master.csv", "claims": "claims.csv", "payments": "koza_furikae.csv"}


def export(facts: dict, system: str, damage: float = 1.0, seed: int = 7) -> tuple[list, list]:
    """(rows, answer key) for one system; ``damage`` scales every kind of dirt (0 = clean)."""
    rng = random.Random(f"export-{system}-{seed}")
    by_id = facts["world"].by_id()
    policies = {p["rec"]: p for p in facts["policies"]}
    rows, key = [], []

    def dirt(sloppiness: float, channel: str) -> Damage:
        return Damage(rng, sloppiness, channel, damage)

    def mention(rec: str, role: str, entity: dict, dmg: Damage) -> None:
        tags = sorted(dmg.tags | set(entity.get("tags", [])))
        key.append(
            {
                "system": system,
                "record_id": rec,
                "role": role,
                "entity_id": entity["id"],
                "tags": ";".join(tags),
            }
        )

    if system == "policies":
        for p in facts["policies"]:
            holder, person = by_id[p["holder"]], by_id[p["holder"]]["kind"] == "person"
            sub, style, as_of = p["sub"], p["date_style"], p["as_of"]
            dmg = dirt(p["sloppiness"], p["channel"])
            row = {
                "REC_ID": p["rec"],
                "SHOKEN_NO": p["number"],
                "SHOHIN_CD": p["code"],
                "KEIYAKU_YMD": render_date(dirt(0, p["channel"]), p["signed"], style),
                "KOSHIN_YMD": max(as_of.values()).strftime("%Y%m%d"),
                "KAIYAKU_YMD": p["ended"].strftime("%Y%m%d") if p["ended"] else "",
                "DAIRITEN_CD": p["agency"],
                "NYURYOKU_KBN": {"paper": "1", "agency": "2", "web": "3"}[p["channel"]],
            }
            fields, holder_addr = role_fields(
                dmg, "KYK", holder, as_of["name"], as_of["addr"], sub, style
            )
            row.update(fields)
            tel = at(holder["addr"], as_of["tel"])[3]
            mobile = at(holder["mobile"], as_of["tel"]) if person else ""
            if (
                tel
                and tel != holder["addr"][-1][1][3]
                or mobile
                and mobile != holder["mobile"][-1][1]
            ):
                dmg.tags.add("stale_phone")
            row["KYK_TEL"], row["KYK_TEL2"] = (
                render_phone(dmg, tel or mobile),
                render_phone(dmg, mobile if tel else ""),
            )
            if row["KYK_NM_KJ"] or row["KYK_NM_KN"]:
                mention(p["rec"], "holder", holder, dmg)
            if p["insured"]:
                insured = by_id[p["insured"]]
                if p["insured_blank"]:
                    row.update(
                        {
                            "HHK_NM_KJ": "",
                            "HHK_NM_KN": "",
                            "HHK_BIRTH": "",
                            "HHK_ZOKU": rng.choice(["本人", "同上", ""]),
                        }
                    )
                else:
                    dmg_i = dirt(p["sloppiness"], p["channel"])
                    when = p["insured_when"]
                    fields, _ = role_fields(
                        dmg_i, "HHK", insured, when, when, sub, style, "name birth"
                    )
                    row.update(fields)
                    female = insured["sex"] == "F"
                    row["HHK_ZOKU"] = (
                        "本人"
                        if insured is holder
                        else rng.choice(
                            ["配偶者", "妻" if female else "夫"]
                            if insured["id"] == holder["spouse"]
                            else ["子", "長女" if female else "長男"]
                        )
                    )
                    if row["HHK_NM_KJ"] or row["HHK_NM_KN"]:
                        mention(p["rec"], "insured", insured, dmg_i)
            if p["beneficiary"]:
                beneficiary = by_id[p["beneficiary"]]
                dmg_b = dirt(p["sloppiness"], p["channel"])
                fields, _ = role_fields(
                    dmg_b,
                    "UKT",
                    beneficiary,
                    p["signed"],
                    p["signed"],
                    sub,
                    style,
                    "name addr",
                    holder_addr,
                )
                row.update(fields)
                row["UKT_ZOKU"] = (
                    "配偶者"
                    if beneficiary["id"] == holder["spouse"]
                    else "子"
                    if beneficiary["id"] in holder["children"]
                    else "親"
                )
                if row["UKT_NM_KJ"] or row["UKT_NM_KN"]:
                    mention(p["rec"], "beneficiary", beneficiary, dmg_b)
            elif p["placeholder"]:
                row["UKT_NM_KJ"] = p["placeholder"]
            rows.append(row)
    elif system == "claims":
        for c in facts["claims"]:
            holder, claimant, policy = (
                by_id[c["holder"]],
                by_id[c["claimant"]],
                policies[c["policy"]],
            )
            received, channel = c["received"], c["channel"]
            sloppiness = {"phone": 1.1, "paper": 1.3, "web": 0.6}[channel]
            dmg = dirt(sloppiness, channel)
            ref = policy["number"]
            if dmg.hit(0.1):
                ref = ""
            elif dmg.hit(0.08, "policy_ref_typo"):
                ref = dmg.digit_typo(ref)
            kanji, kana = render_name(dmg, claimant, received, "claims")
            address = at(claimant["addr"], received)
            if address != claimant["addr"][-1][1]:
                dmg.tags.add("stale_address")
            addr_text, zip_code = render_address(dmg, address, received)
            mobile = at(claimant["mobile"], received) if claimant["kind"] == "person" else ""
            relation = (
                "法人"
                if claimant["kind"] == "company"
                else "本人"
                if claimant is holder
                else "配偶者"
                if claimant["id"] == holder.get("spouse")
                else "子"
            )
            note = ""
            if rng.random() < 0.25 and claimant["kind"] == "person":
                old_names = [n for _, n in claimant["names"][:-1]]
                old_addr = at(claimant["addr"], policy["signed"])
                templates = [f"{relation}より入電。", "折返し希望。"]
                if claimant is not holder:
                    templates.append(
                        f"契約者{holder['names'][-1][1][0]}様の件、{relation}より請求。"
                    )
                if old_names:
                    templates.append(f"旧姓{old_names[-1][0]}。")
                if old_addr != address:
                    old_text = render_address(dirt(0.5, channel), old_addr, policy["signed"])[0]
                    templates.append(f"住所変更あり（旧：{old_text}）。")
                note = rng.choice(templates)
            row = {
                "REC_ID": c["rec"],
                "UKETSUKE_NO": c["uketsuke"],
                "SHOKEN_NO": ref,
                "JIKO_YMD": c["happened"].strftime("%Y/%m/%d"),
                "UKETSUKE_YMD": received.strftime("%Y/%m/%d"),
                "UKETSUKE_KBN": {"phone": "電話", "paper": "書面", "web": "WEB"}[channel],
                "SEIKYU_NM": kanji,
                "SEIKYU_KANA": hira(kana) if channel == "web" and rng.random() < 0.5 else kana,
                "SEIKYU_ZOKU": relation,
                "SEIKYU_TEL": render_phone(dmg, mobile or address[3]),
                "SEIKYU_ADDR": addr_text,
                "SEIKYU_ZIP": zip_code,
                "BIKO": note,
            }
            if row["SEIKYU_NM"] or row["SEIKYU_KANA"]:
                mention(c["rec"], "claimant", claimant, dmg)
            if c["insured"]:
                insured = by_id[c["insured"]]
                dmg_h = dirt(sloppiness, channel)
                kanji, kana = render_name(dmg_h, insured, received, "claims")
                row.update(
                    {
                        "HHK_NM": kanji,
                        "HHK_KANA": kana,
                        "HHK_BIRTH": render_date(dmg_h, insured["dob"], "west"),
                        "HHK_SEX": "" if dmg_h.hit(0.1) else {"M": "男", "F": "女"}[insured["sex"]],
                    }
                )
                if kanji or kana:
                    mention(c["rec"], "insured", insured, dmg_h)
            rows.append(row)
    elif system == "payments":
        # Exported in account-registration order, not policy order.
        for pay in sorted(facts["payments"], key=lambda r: (r["when"], r["rec"])):
            entity = by_id[pay["entity"]]
            account = entity["accounts"][pay["account"]]
            dmg = dirt(1.0, "bank")
            bank, branch, number = account["number"]
            if dmg.hit(0.02, "account_typo"):
                number = dmg.digit_typo(number)
            rows.append(
                {
                    "REC_ID": pay["rec"],
                    "KANRI_NO": pay["kanri"] if not dmg.hit(0.03) else "",
                    "SHUBETSU": pay["kind"],
                    "GINKO_CD": bank,
                    "SHITEN_CD": branch,
                    "KAMOKU": "1" if entity["kind"] == "person" else rng.choice("12"),
                    "KOZA_NO": number.lstrip("0") if dmg.hit(0.05) else number,
                    "KOZA_MEIGI": zengin_name(dmg, entity, account, pay["when"]),
                    "TOROKU_YMD": pay["when"].strftime("%Y%m%d"),
                }
            )
            mention(pay["rec"], "account_holder", entity, dmg)
    else:
        raise ValueError(f"Unknown system {system!r}; use one of {', '.join(SYSTEMS)}.")
    return rows, key


COLUMNS = {}  # filled below, once the column lists are defined


def write_export(
    system: str, rows: list, key: list, world: World, data_dir: Path, key_dir: Path
) -> dict:
    """Write one system's dirty file (plus the IT note) and its part of the hidden answer key."""
    data_dir.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)
    encoding = "utf-8-sig" if system == "claims" else "cp932"
    write_csv(data_dir / SYSTEMS[system], COLUMNS[system], rows, encoding, "\r\n")
    (data_dir / "README.txt").write_text(README, encoding="utf-8")
    write_csv(
        key_dir / f"answer_key_{system}.csv",
        ["system", "record_id", "role", "entity_id", "tags"],
        key,
        "utf-8",
        "\n",
    )
    entities = [
        {
            "entity_id": e["id"],
            "kind": e["kind"],
            "household": e.get("hh", ""),
            "name_kana": (e["names"][-1][1][1] + e["given"][1])
            if e["kind"] == "person"
            else e["core"][1],
            "dob": e["dob"].isoformat() if e["kind"] == "person" else "",
            "tags": ";".join(e.get("tags", [])),
        }
        for e in world.people + world.companies
    ]
    write_csv(
        key_dir / "entities.csv",
        ["entity_id", "kind", "household", "name_kana", "dob", "tags"],
        entities,
        "utf-8",
        "\n",
    )
    return tag_counts(key)


def tag_counts(key: list) -> dict:
    counts = {}
    for row in key:
        for tag in filter(None, row["tags"].split(";")):
            counts[tag] = counts.get(tag, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def generate(
    data_dir: Path,
    key_dir: Path,
    households: int = 1200,
    seed: int = 7,
    damage: float = 1.0,
    update_rate: float = 0.5,
) -> dict:
    """All three stages with default settings, as the eval harness and tests use them."""
    world = population(households, seed)
    facts = activity(world, seed, update_rate)
    stats = {"tags": {}}
    mentions = 0
    for system in SYSTEMS:
        rows, key = export(facts, system, damage, seed)
        for tag, n in write_export(system, rows, key, world, data_dir, key_dir).items():
            stats["tags"][tag] = stats["tags"].get(tag, 0) + n
        stats[system] = len(rows)
        mentions += len(key)
    stats["mentions"] = mentions
    (key_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    return stats


POLICY_COLUMNS = [
    "REC_ID",
    "SHOKEN_NO",
    "SHOHIN_CD",
    "KEIYAKU_YMD",
    "KOSHIN_YMD",
    "KAIYAKU_YMD",
    "DAIRITEN_CD",
    "NYURYOKU_KBN",
    "KYK_NM_KJ",
    "KYK_NM_KN",
    "KYK_SEX",
    "KYK_BIRTH",
    "KYK_ZIP",
    "KYK_ADDR",
    "KYK_TEL",
    "KYK_TEL2",
    "HHK_NM_KJ",
    "HHK_NM_KN",
    "HHK_BIRTH",
    "HHK_ZOKU",
    "UKT_NM_KJ",
    "UKT_NM_KN",
    "UKT_ZOKU",
    "UKT_ADDR",
]
CLAIM_COLUMNS = [
    "REC_ID",
    "UKETSUKE_NO",
    "SHOKEN_NO",
    "JIKO_YMD",
    "UKETSUKE_YMD",
    "UKETSUKE_KBN",
    "SEIKYU_NM",
    "SEIKYU_KANA",
    "SEIKYU_ZOKU",
    "SEIKYU_TEL",
    "SEIKYU_ZIP",
    "SEIKYU_ADDR",
    "HHK_NM",
    "HHK_KANA",
    "HHK_BIRTH",
    "HHK_SEX",
    "BIKO",
]
PAYMENT_COLUMNS = [
    "REC_ID",
    "KANRI_NO",
    "SHUBETSU",
    "GINKO_CD",
    "SHITEN_CD",
    "KAMOKU",
    "KOZA_NO",
    "KOZA_MEIGI",
    "TOROKU_YMD",
]
COLUMNS.update(policies=POLICY_COLUMNS, claims=CLAIM_COLUMNS, payments=PAYMENT_COLUMNS)


def write_csv(path: Path, columns: list, records: list, encoding: str, newline: str) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, columns, extrasaction="ignore", lineterminator=newline)
    writer.writeheader()
    writer.writerows(records)
    # Legacy exports replace what Shift_JIS cannot hold with "?".
    path.write_bytes(buffer.getvalue().encode(encoding, errors="replace"))


README = """顧客データ抽出（名寄せ用） / Customer data export for nayose

情報システム部より。3システムから抽出したデータです。品質は保証できません。
From IT: exports from three systems. We cannot vouch for the data quality.

1. policy_master.csv  契約管理システム（Shift_JIS, CRLF）1行=1契約
   自動車・火災は旧ホスト系、医療・傷害は2000年代のシステム、個人賠償はWEB契約。
   Auto and fire come from the old mainframe; medical and injury from a 2000s system;
   personal liability from the web channel. Columns:
   REC_ID 抽出連番 (row id) / SHOKEN_NO 証券番号 / SHOHIN_CD 商品 (01 自動車, 02 火災, 03 医療,
   04 傷害, 05 個人賠償) / KEIYAKU_YMD 契約日 / KOSHIN_YMD 最終更新日 / KAIYAKU_YMD 解約日 /
   DAIRITEN_CD 代理店 / NYURYOKU_KBN 入力区分 (1 紙申込書, 2 代理店端末, 3 WEB)
   KYK_* 契約者 (policy holder): NM_KJ 漢字氏名, NM_KN カナ氏名, SEX (1 男, 2 女), BIRTH 生年月日,
       ZIP, ADDR 住所, TEL, TEL2. 法人契約あり (companies can be holders).
   HHK_* 被保険者 (insured person), ZOKU = 契約者から見た続柄 (relation to the holder)
   UKT_* 死亡保険金受取人 (beneficiary). 「法定相続人」「未指定」は特定の人ではない
       (legal heirs / not designated: not a specific person).
   Dates are in Japanese era form in some systems (e.g. S45.03.12, or 3450312 = 昭和45年3月12日).

2. claims.csv  事故受付システム（UTF-8 with BOM, Excel export）1行=1事故受付
   UKETSUKE_NO 受付番号 / SHOKEN_NO 証券番号（受付者入力・誤りや空欄あり; typed by staff, may be
   wrong or empty）/ JIKO_YMD 事故日 / UKETSUKE_YMD 受付日 / UKETSUKE_KBN 電話・書面・WEB
   SEIKYU_* 請求者 (claimant), ZOKU = 契約者との続柄 / HHK_* 被保険者 (insured person)
   BIKO 備考（自由記述 / free text）

3. koza_furikae.csv  口座管理システム（Shift_JIS, CRLF）1行=1口座登録
   KANRI_NO 証券番号 or 事故受付番号 / SHUBETSU (1 保険料口座振替 premium debit,
   2 保険金支払 claim payout) / GINKO_CD 銀行 / SHITEN_CD 支店 / KAMOKU (1 普通, 2 当座) /
   KOZA_NO 口座番号 / KOZA_MEIGI 口座名義（全銀形式の半角カナ; bank-format kana）/ TOROKU_YMD 登録日
   口座名義人は契約者本人とは限らない (the account holder is not always the policy holder).

Person mentions (one per row and role, when the row names someone):
  policy_master.csv: holder (KYK_*), insured (HHK_*), beneficiary (UKT_*)
  claims.csv: claimant (SEIKYU_*), insured (HHK_*)
  koza_furikae.csv: account_holder (KOZA_MEIGI)
A mention exists when its kanji or kana name field is not empty, except the non-person
beneficiary values above. system names: policies, claims, payments; record_id = REC_ID.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("key_dir", type=Path)
    parser.add_argument("--households", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    print(
        json.dumps(
            generate(args.data_dir, args.key_dir, args.households, args.seed),
            ensure_ascii=False,
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
