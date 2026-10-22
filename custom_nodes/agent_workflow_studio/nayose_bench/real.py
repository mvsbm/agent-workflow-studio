"""Names, towns and companies from public lists, for the lifelike synthetic profiles (sources.py's
local cache).

RealWorld draws surnames and given names by their frequency (Nemotron-Personas-Japan) with
dictionary readings (JMnedict: a name's other readings are what a clerk guesses), prefectures as the
census has them, and towns with their kana (Japan Post) at synthetic block numbers. Companies
come from the National Tax Agency's registry (name, furigana, head-office address); some people run
a shop under a trade name (sole proprietors). Without the cache, the built-in pools are used.
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from pathlib import Path

from . import generate as g
from .sources import CACHE

D = g.D
# One representative area code per prefecture (real numbers vary within a prefecture).
PREF_AREA = dict(
    x.split(":")
    for x in (
        "北海道:011 青森県:017 岩手県:019 宮城県:022 秋田県:018 山形県:023 福島県:024 茨城県:029 栃木県:028 "
        "群馬県:027 埼玉県:048 千葉県:043 東京都:03 神奈川県:045 新潟県:025 富山県:076 石川県:076 福井県:0776 "
        "山梨県:055 長野県:026 岐阜県:058 静岡県:054 愛知県:052 三重県:059 滋賀県:077 京都府:075 大阪府:06 "
        "兵庫県:078 奈良県:0742 和歌山県:073 鳥取県:0857 島根県:0852 岡山県:086 広島県:082 山口県:083 "
        "徳島県:088 香川県:087 愛媛県:089 高知県:088 福岡県:092 佐賀県:0952 長崎県:095 熊本県:096 大分県:097 "
        "宮崎県:0985 鹿児島県:099 沖縄県:098"
    ).split()
)
TRADES = [
    ("商店", "ショウテン"),
    ("工務店", "コウムテン"),
    ("美容室", "ビヨウシツ"),
    ("理容店", "リヨウテン"),
    ("歯科医院", "シカイイン"),
    ("クリニック", "クリニック"),
    ("税理士事務所", "ゼイリシジムショ"),
    ("食堂", "ショクドウ"),
    ("酒店", "サケテン"),
    ("電器", "デンキ"),
    ("農園", "ノウエン"),
    ("不動産", "フドウサン"),
    ("塗装", "トソウ"),
    ("鍼灸院", "シンキュウイン"),
    ("書道教室", "ショドウキョウシツ"),
]
LEGAL_SHORT = {
    "株式会社": ("(株)", "㈱", "カ)"),
    "有限会社": ("(有)", "㈲", "ユ)"),
    "合同会社": ("(同)", "(同)", "ド)"),
    "合名会社": ("(名)", "(名)", "メ)"),
    "合資会社": ("(資)", "(資)", "シ)"),
    "その他": ("", "", ""),
}
LEGAL_KANA = {
    "株式会社": "カブシキガイシャ",
    "有限会社": "ユウゲンガイシャ",
    "合同会社": "ゴウドウガイシャ",
    "合名会社": "ゴウメイガイシャ",
    "合資会社": "ゴウシガイシャ",
    "その他": "",
}


class Sources:
    """The cached tables (None when sources.py has not been run)."""

    def __init__(self, cache: Path = CACHE):
        def rows(name):
            with open(cache / name, encoding="utf-8") as f:
                return list(csv.DictReader(f, delimiter="\t"))

        self.surnames = [
            (r["kanji"], r["readings"].split("|"), int(r["count"])) for r in rows("surnames.tsv")
        ]
        self.given = defaultdict(list)
        for r in rows("given.tsv"):
            self.given[r["sex"]].append((r["kanji"], r["readings"].split("|"), int(r["count"])))
        self.towns = rows("towns.tsv")
        self.towns_by_pref = defaultdict(list)
        self.towns_by_city = defaultdict(list)
        for i, t in enumerate(self.towns):
            self.towns_by_pref[t["pref"]].append(i)
            self.towns_by_city[(t["pref"], t["city"])].append(i)
        pref = defaultdict(int)
        for r in rows("people.tsv"):
            pref[r["prefecture"]] += 1
        self.pref_weights = [(p, c) for p, c in pref.items() if p in self.towns_by_pref]
        self.companies = rows("companies.tsv")
        events = {
            n: rows(n + ".tsv") if (cache / f"{n}.tsv").exists() else []
            for n in ("renames", "moves", "mergers", "same_name", "groups")
        }
        self.renames, self.moves, self.mergers = (
            events["renames"],
            events["moves"],
            events["mergers"],
        )
        self.same_name, self.groups = defaultdict(list), defaultdict(list)
        for r in events["same_name"]:
            self.same_name[r["group"]].append(r)
        for r in events["groups"]:
            self.groups[r["group"]].append(r)

    @classmethod
    def load(cls) -> Sources | None:
        return cls() if (CACHE / "companies.tsv").exists() else None


class RealWorld(g.World):
    """The population with real names and towns."""

    def __init__(self, rng: random.Random, src: Sources):
        super().__init__(rng)
        self.src = src
        self._sur_w = [c for _, _, c in src.surnames]
        self._giv_w = {s: [c for _, _, c in v] for s, v in src.given.items()}
        self._prefs, self._pref_w = zip(*src.pref_weights, strict=True)

    def surname(self) -> tuple:
        kanji, reads, _ = self.rng.choices(self.src.surnames, self._sur_w)[0]
        # (kanji, its reading, what a clerk guesses: another reading of the same kanji)
        return (kanji, reads[0], reads[1] if len(reads) > 1 else reads[0])

    def given(self, sex: str, born: D) -> tuple:
        kanji, reads, _ = self.rng.choices(self.src.given[sex], self._giv_w[sex])[0]
        return (kanji, reads[0])

    def address(self, place, since: D) -> tuple:
        rng = self.rng
        if isinstance(place, int) or (place is None and rng.random() < 0.1):
            return super().address(place, since)  # the curated towns with municipal mergers
        if place is None:
            town = rng.choice(self.src.towns_by_pref[rng.choices(self._prefs, self._pref_w)[0]])
        else:  # moving nearby: the same town or another town of the same municipality
            t = self.src.towns[place[1]]
            town = (
                place[1]
                if rng.random() < 0.5
                else rng.choice(self.src.towns_by_city[(t["pref"], t["city"])])
            )
        t = self.src.towns[town]
        numbers = (
            (rng.randint(1, 6), rng.randint(1, 30), rng.randint(1, 25))
            if t["chome"] == "1"
            else (rng.randint(1, 2999),)
            if rng.random() < 0.6
            else (rng.randint(1, 999), rng.randint(1, 30))
        )
        building = (
            f"{rng.choice(g.BUILDINGS)}{rng.randint(1, 9)}0{rng.randint(1, 9)}"
            if rng.random() < 0.4
            else ""
        )
        landline = ""
        if rng.random() < (0.7 if since.year < 2005 else 0.3):
            landline = self.landline(t["pref"])
        return (("T", town), numbers, building, landline)

    def pref_of(self, address: tuple) -> str:
        place = address[0]
        return self.src.towns[place[1]]["pref"] if isinstance(place, tuple) else g.PLACES[place][0]

    def landline(self, pref: str) -> str:
        rng, area = self.rng, PREF_AREA.get(pref, "03")
        exchange = 10 - len(area) - 4
        return self.unique(
            lambda: (
                f"{area}-{rng.randint(10 ** (exchange - 1) * 2, 10**exchange - 1)}-{rng.randint(0, 9999):04d}"
            )
        )

    def shops(self, share: float = 0.04) -> None:
        """Some adults run a shop under a trade name (a sole proprietor)."""
        rng = self.rng
        for p in self.people:
            age = (g.END - p["dob"]).days // 365
            if 25 <= age <= 80 and rng.random() < share:
                trade, trade_kana = rng.choice(TRADES)
                sur_k, sur_n, _ = p["names"][-1][1]
                giv_k, giv_n = p["given"]
                base_k, base_n = (sur_k, sur_n) if rng.random() < 0.7 else (giv_k, giv_n)
                founded = g.rand_date(rng, g.plus_years(p["dob"], 22), g.END)
                home = p["addr"][-1][1]
                p["shop"] = {
                    "name": base_k + trade,
                    "kana": base_n + trade_kana,
                    "founded": founded,
                    "phone": self.landline(self.pref_of(home)),
                    "address": home if rng.random() < 0.6 else self.address(None, founded),
                }
                p["tags"].append("sole_proprietor")

    def firm(self, row: dict, founded: D | None = None, **extra) -> dict:
        """A company entity from a registry row: its name and head office over time, phones
        (main, branches, sometimes a FAX and a toll-free number), branches."""
        rng = self.rng
        founded = founded or g.rand_date(rng, D(1950, 1, 1), D(2024, 12, 31))
        head = {"pref": row["pref"], "city": row["city"], "street": row["street"]}
        branches = [self.address(None, founded) for _ in range(rng.choice([0, 0, 0, 1, 1, 2, 3]))]
        main = extra.pop("main_phone", None) or self.landline(row["pref"])
        c = {
            "id": f"C{len(self.firm_list) + 1:05d}",
            "kind": "company",
            "names": [(founded, row["name"], row["kana"])],
            "name": row["name"],
            "kana": row["kana"],
            "english": row.get("english", ""),
            "legal": row["kind"],
            "founded": founded,
            "heads": [(founded, head)],
            "head": head,
            "branches": branches,
            "phones": [main] + [self.landline(self.pref_of(b)) for b in branches],
            "fax": self.landline(row["pref"]) if rng.random() < 0.5 else "",
            "tollfree": self.unique(lambda: f"0120-{rng.randint(100, 999)}-{rng.randint(100, 999)}")
            if rng.random() < 0.15
            else "",
            "tags": [],
        }
        c.update(extra)
        self.firm_list.append(c)
        return c

    def firms_from_registry(self, count: int, events: float = 1.0) -> list[dict]:
        """Real companies, and real company events at their realistic share: renamed, moved,
        merged (the absorbed company is its own closed entity linked to its successor), same-name
        strangers, and sibling companies sharing a building and switchboard."""
        rng, src = self.rng, self.src
        self.firm_list = []
        plain = int(count * (1 - 0.3 * events))
        for row in rng.sample(src.companies, min(plain, len(src.companies))):
            self.firm(row)
        n = max(1, int(count * 0.06 * events))
        for r in rng.sample(src.renames, min(n, len(src.renames))):
            c = self.firm(
                {
                    "name": r["old_name"],
                    "kana": r["new_kana"],
                    "kind": r["kind"],
                    "pref": r["pref"],
                    "city": r["city"],
                    "street": r["street"],
                }
            )
            when = g.rand_date(rng, max(c["founded"], g.START), g.END)
            c["names"].append((when, r["new_name"], r["new_kana"]))
            c["tags"].append("company_renamed")
        for r in rng.sample(src.moves, min(n, len(src.moves))):
            c = self.firm(
                {
                    "name": r["name"],
                    "kana": r["kana"],
                    "kind": r["kind"],
                    "pref": r["old_pref"],
                    "city": r["old_city"],
                    "street": r["old_street"],
                }
            )
            when = g.rand_date(rng, max(c["founded"], g.START), g.END)
            c["heads"].append((when, {"pref": r["pref"], "city": r["city"], "street": r["street"]}))
            c["tags"].append("company_moved")
        for r in rng.sample(src.mergers, min(n, len(src.mergers))):
            successor = self.firm({k[10:]: v for k, v in r.items() if k.startswith("successor_")})
            absorbed = self.firm(
                {
                    "name": r["name"],
                    "kana": r["kana"],
                    "kind": r["kind"],
                    "pref": r["pref"],
                    "city": r["city"],
                    "street": r["street"],
                }
            )
            closed = g.rand_date(rng, max(absorbed["founded"], g.START), g.END)
            absorbed.update(closed=closed, successor=successor["id"])
            absorbed["tags"].append("merged_away")
            if rng.random() < 0.3:  # the absorbed company's number lives on at the successor
                successor["phones"].append(absorbed["phones"][0])
        for group in rng.sample(list(src.same_name.values()), min(n, len(src.same_name))):
            for r in group[:3]:
                self.firm(r)["tags"].append("same_name_company")
        for group in rng.sample(list(src.groups.values()), min(n, len(src.groups))):
            switchboard = self.landline(group[0]["pref"])
            for r in group[:4]:
                self.firm(r, main_phone=switchboard)["tags"].append("group_company")
        return self.firm_list
