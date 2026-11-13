from collections import Counter, defaultdict
from statistics import mean, median
import math

from .validation import require_tables, unique, finite, references


def validate(data):
    require_tables(data, ["authors", "affiliations", "works"])
    unique(data["authors"], ["author_id"])
    unique(data["works"], ["work_id"])
    references(data["affiliations"], "author_id", data["authors"], "author_id")
    known = {author["author_id"] for author in data["authors"]}
    for work in data["works"]:
        if not set(work["author_ids"]) <= known or len(set(work["author_ids"])) != len(work["author_ids"]):
            raise ValueError("作品作者不存在或重复")
        if not set(work["roles"]) <= set(work["author_ids"]):
            raise ValueError("贡献角色引用了未署名作者")
        finite(work["year"], minimum=1900, maximum=2200)
    for record in data["affiliations"]:
        if record["end"] < record["start"]:
            raise ValueError("机构有效期倒置")


def explore(data, selection):
    try:
        year = int(selection.get("year", "2025"))
    except (ValueError, TypeError):
        raise ValueError("年份应为整数") from None
    works = [work for work in data["works"] if work["year"] == year]
    if not works:
        raise ValueError("该年份没有作品")
    productivity, partners = Counter(), defaultdict(set)
    for work in works:
        for author in work["author_ids"]:
            productivity[author] += 1
            partners[author].update(set(work["author_ids"]) - {author})
    rows = [{"author_id": author, "works": productivity[author], "distinct_partners": len(partners[author]),
             "institutions": [record["institution"] for record in data["affiliations"]
                              if record["author_id"] == author and record["start"] <= year <= record["end"]]}
            for author in sorted(productivity)]
    return {"summary": {"作品": len(works), "活跃作者": len(rows), "最大团队": max(len(work["author_ids"]) for work in works)},
            "rows": rows, "series": [{"label": row["author_id"], "y": row["distinct_partners"]} for row in rows],
            "notes": ["伙伴数按共同署名去重，不代表合作强度或独立事件。", "机构使用所选年度生效记录，作者编号不因流动改变。"]}
