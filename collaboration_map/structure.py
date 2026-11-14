"""年度合作结构比较。

在可选时间窗口与贡献定义下构建加权署名网络，识别有实际协作依据的群体，
追踪相邻窗口间群体的延续、合并、分散与成员流动，并与保持作者活跃度
和团队规模特征的参照样本比较，避免把偶然连边解读为组织变化。

设计约定：
- 贡献定义（整对/分摊/均摊）与时间窗口由分析人员选择，随结果一并保存；
  不默认把一篇多人作品折算为大量独立合作。
- 群体为确定性模块度贪心分组（单层局部移动），同一数据与定义结果可复现。
- 相邻窗口间仅当成员 Jaccard 重叠达到阈值才沿用群体标签；成员变动过大、
  无法可靠续接的群体启用新标签，不为画出连续曲线强行沿用旧名称。
- 参照模型保持各窗口作者活跃度（人均作品数）与团队规模（各作品人数）
  不变，随机重配署名关系；随机种子由数据内容与窗口定义决定，可复现。
"""

from collections import Counter, defaultdict
import hashlib
import random

from .storage import encode

MATCH_THRESHOLD = 0.5
MAX_PASSES = 60
MAX_WINDOWS = 20
NULL_ATTEMPTS = 200

WEIGHTING_CHOICES = {
    "分摊": "fractional",
    "整对": "pairs",
    "均摊": "uniform",
    "fractional": "fractional",
    "pairs": "pairs",
    "uniform": "uniform",
}
WEIGHTING_NAMES = {"fractional": "分摊", "pairs": "整对", "uniform": "均摊"}
WEIGHTING_NOTES = {
    "fractional": "分摊定义下，一篇 n 人作品对每个署名对计 1/(n-1)，全篇合计 n/2，每位署名者合计 1。",
    "pairs": "整对定义下，一篇 n 人作品展开为 n(n-1)/2 个署名对且各计 1，大型合著会被计为大量成对合作。",
    "uniform": "均摊定义下，一篇 n 人作品总权重恒为 1，每个署名对计 2/(n(n-1))。",
}


def _pair_weight(kind, size):
    if kind == "pairs":
        return 1.0
    if kind == "fractional":
        return 1.0 / (size - 1)
    return 2.0 / (size * (size - 1))


def _parse_window(value):
    try:
        size = int(str(value))
    except (ValueError, TypeError):
        raise ValueError("窗口年数应为整数") from None
    if not 1 <= size <= 5:
        raise ValueError("窗口年数应在1至5之间")
    return size


def _parse_samples(value):
    try:
        samples = int(str(value))
    except (ValueError, TypeError):
        raise ValueError("参照样本数应为整数") from None
    if not 0 <= samples <= 1000:
        raise ValueError("参照样本数应在0至1000之间")
    return samples


def _parse_weighting(value):
    key = str(value)
    if key not in WEIGHTING_CHOICES:
        raise ValueError("未知贡献定义：" + key)
    return WEIGHTING_CHOICES[key]


def _round(value, digits=4):
    return None if value is None else round(value, digits)


def _window_label(years):
    return str(years[0]) if len(years) == 1 else "%d-%d" % (years[0], years[-1])


def _institutions(records, years):
    return {record["institution"] for record in records
            if record["start"] <= years[-1] and record["end"] >= years[0]}


def _build_edges(works, kind):
    edges = defaultdict(float)
    solo = 0
    for work in works:
        ids = sorted(work["author_ids"])
        size = len(ids)
        if size < 2:
            solo += 1
            continue
        weight = _pair_weight(kind, size)
        for i in range(size):
            for j in range(i + 1, size):
                edges[(ids[i], ids[j])] += weight
    adjacency = defaultdict(dict)
    for (u, v), weight in edges.items():
        adjacency[u][v] = weight
        adjacency[v][u] = weight
    return dict(edges), {node: dict(row) for node, row in adjacency.items()}, solo


def _detect_communities(adjacency):
    """确定性单层模块度贪心：节点按编号序遍历，移动到增益最大的邻居群体。"""
    nodes = sorted(adjacency)
    if not nodes:
        return {}
    degree = {node: sum(adjacency[node].values()) for node in nodes}
    total2 = sum(degree.values())
    community = {node: index for index, node in enumerate(nodes)}
    totals = {index: degree[node] for index, node in enumerate(nodes)}
    for _ in range(MAX_PASSES):
        moved = False
        for node in nodes:
            current = community[node]
            weights = defaultdict(float)
            for neighbor, weight in adjacency[node].items():
                weights[community[neighbor]] += weight
            totals[current] -= degree[node]
            best = current
            best_gain = weights.get(current, 0.0) - degree[node] * totals[current] / total2
            for candidate in sorted(weights):
                if candidate == current:
                    continue
                gain = weights[candidate] - degree[node] * totals[candidate] / total2
                if gain > best_gain + 1e-12:
                    best, best_gain = candidate, gain
            community[node] = best
            totals[best] = totals.get(best, 0.0) + degree[node]
            if best != current:
                moved = True
        if not moved:
            break
    return community


def _modularity(adjacency, partition):
    total2 = sum(sum(row.values()) for row in adjacency.values())
    if total2 == 0:
        return 0.0
    internal = defaultdict(float)
    totals = defaultdict(float)
    for node, row in adjacency.items():
        own = partition[node]
        totals[own] += sum(row.values())
        for neighbor, weight in row.items():
            if partition[neighbor] == own:
                internal[own] += weight
    return sum(internal[comm] / total2 - (totals[comm] / total2) ** 2 for comm in totals)


def _graph_metrics(adjacency, edges, inst_map):
    partition = _detect_communities(adjacency)
    groups = defaultdict(list)
    for node, comm in partition.items():
        groups[comm].append(node)
    communities = [sorted(members) for members in groups.values() if len(members) >= 2]
    communities.sort(key=lambda members: (-len(members), members))
    member_of = {}
    for index, members in enumerate(communities):
        for author in members:
            member_of[author] = index
    total = sum(edges.values())
    intra = sum(weight for (u, v), weight in edges.items()
                if member_of.get(u) is not None and member_of.get(u) == member_of.get(v))
    known = cross = 0.0
    for (u, v), weight in edges.items():
        inst_u, inst_v = inst_map.get(u), inst_map.get(v)
        if inst_u and inst_v:
            known += weight
            if inst_u.isdisjoint(inst_v):
                cross += weight
    return {
        "partition": partition,
        "communities": communities,
        "total_weight": total,
        "intra_weight": intra,
        "q": _modularity(adjacency, partition),
        "cross_group_share": (total - intra) / total if total > 0 else None,
        "cross_inst_share": cross / known if known > 0 else None,
        "inst_coverage": known / total if total > 0 else None,
    }


def _draw_assignment(sizes, activity, authors, rng):
    """在保持作者活跃度与团队规模不变下随机重配署名；不可行时返回 None。"""
    remaining = {author: activity[author] for author in authors}
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i], i))
    assignment = [None] * len(sizes)
    left = len(sizes)
    for index in order:
        need = sizes[index]
        left -= 1
        pool = [author for author in authors if remaining[author] > 0]
        if len(pool) < need:
            return None
        keys = sorted((-(rng.random() ** (1.0 / remaining[author])), author) for author in pool)
        chosen = [author for _, author in keys[:need]]
        for author in chosen:
            remaining[author] -= 1
        assignment[index] = chosen
        if any(remaining[author] > left for author in authors):
            return None
    return assignment


def _null_metrics(sizes, activity, inst_map, kind, samples, seed):
    rng = random.Random(seed)
    authors = sorted(activity)
    collected = []
    failures = 0
    for _ in range(samples):
        assignment = None
        for _ in range(NULL_ATTEMPTS):
            candidate = _draw_assignment(sizes, activity, authors, rng)
            if candidate is not None:
                assignment = candidate
                break
        if assignment is None:
            failures += 1
            continue
        works = [{"work_id": "null_%d" % index, "author_ids": chosen}
                 for index, chosen in enumerate(assignment)]
        edges, adjacency, _ = _build_edges(works, kind)
        collected.append(_graph_metrics(adjacency, edges, inst_map))
    return collected, failures


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int(fraction * (len(ordered) - 1))]


def _community_evidence(window_works, members, kind):
    member_set = set(members)
    internal = 0.0
    support = []
    for work in window_works:
        inside = sum(1 for author in work["author_ids"] if author in member_set)
        if inside < 2:
            continue
        size = len(work["author_ids"])
        contribution = inside * (inside - 1) / 2 * _pair_weight(kind, size)
        internal += contribution
        support.append({"work_id": work["work_id"], "年份": work["year"], "团队规模": size,
                        "群内人数": inside, "贡献权重": round(contribution, 4)})
    support.sort(key=lambda item: (-item["贡献权重"], item["work_id"]))
    return internal, support


def _analyze_window(label, years, works, kind, affiliations, samples):
    activity = Counter()
    for work in works:
        for author in work["author_ids"]:
            activity[author] += 1
    edges, adjacency, solo = _build_edges(works, kind)
    inst_map = {author: _institutions(affiliations.get(author, ()), years) for author in activity}
    metrics = _graph_metrics(adjacency, edges, inst_map)
    grouped = set()
    for members in metrics["communities"]:
        grouped.update(members)
    null_metrics, null_failures = [], 0
    if samples and edges:
        seed = int.from_bytes(hashlib.sha256(encode({
            "window": label, "kind": kind,
            "sizes": sorted(len(work["author_ids"]) for work in works),
            "activity": sorted(activity.items()),
        }).encode("utf-8")).digest()[:8], "big")
        null_metrics, null_failures = _null_metrics(
            [len(work["author_ids"]) for work in works], activity, inst_map, kind, samples, seed)
    return {
        "label": label,
        "years": years,
        "works": works,
        "work_count": len(works),
        "activity": activity,
        "mean_size": sum(len(work["author_ids"]) for work in works) / len(works),
        "solo": solo,
        "inst_map": inst_map,
        "ungrouped": sorted(set(adjacency) - grouped),
        "isolated": sorted(set(activity) - set(adjacency)),
        "roles_works": sum(1 for work in works if work.get("roles")),
        "roles_assignments": sum(len(work.get("roles", {})) for work in works),
        "assignments": sum(len(work["author_ids"]) for work in works),
        "null_metrics": null_metrics,
        "null_failures": null_failures,
        **metrics,
    }


def _track_communities(windows):
    """为各窗口群体分配标签并生成结构事件；返回事件列表。

    标签继承：把所有 Jaccard ≥ 阈值的（前窗群体, 本窗群体）对按重叠度
    全局贪心匹配，每个标签至多被一个后继沿用；重叠不足或匹配失败的
    群体启用新标签，不强行续名。
    """
    events = []
    next_number = 1
    previous = []
    for window in windows:
        current = []
        for members in window["communities"]:
            member_set = set(members)
            qualified = []
            best_label, best_j = None, 0.0
            for prev in previous:
                union = len(member_set | prev["members"])
                jaccard = len(member_set & prev["members"]) / union if union else 0.0
                if jaccard > best_j:
                    best_label, best_j = prev["label"], jaccard
                if jaccard >= MATCH_THRESHOLD:
                    qualified.append((jaccard, prev["label"]))
            qualified.sort(key=lambda item: (-item[0], item[1]))
            current.append({"members": member_set, "qualified": qualified,
                            "best_label": best_label, "best_j": best_j})
        pairs = sorted((-jaccard, label, index)
                       for index, curr in enumerate(current)
                       for jaccard, label in curr["qualified"])
        taken = set()
        for _, label, index in pairs:
            if label in taken or "label" in current[index]:
                continue
            current[index]["label"] = label
            taken.add(label)
        for curr in current:
            if "label" not in curr:
                curr["label"] = "C%d" % next_number
                next_number += 1
        if previous:
            pair_label = "%s→%s" % (window["prev_label"], window["label"])
            prev_by_label = {prev["label"]: prev for prev in previous}
            for curr in current:
                inherited = curr["label"] in prev_by_label
                if inherited and len(curr["qualified"]) >= 2:
                    labels = sorted(label for _, label in curr["qualified"])
                    curr["source"] = "合并自 " + "+".join(labels)
                    events.append({"窗口对": pair_label, "事件": "合并",
                                   "涉及": "+".join(labels) + "→" + curr["label"],
                                   "说明": "%s 合并为 %s（沿用 %s）"
                                           % ("、".join(labels), curr["label"], curr["label"]),
                                   "_members": {curr["label"]: sorted(curr["members"])}})
                elif inherited:
                    curr["source"] = "延续自 " + curr["label"]
                    prev = prev_by_label[curr["label"]]
                    joined = sorted(curr["members"] - prev["members"])
                    left = sorted(prev["members"] - curr["members"])
                    curr["membership_change"] = {"加入": joined, "离开": left}
                    events.append({"窗口对": pair_label, "事件": "延续",
                                   "涉及": curr["label"],
                                   "说明": "%s 延续（加入 %d 人、离开 %d 人）"
                                           % (curr["label"], len(joined), len(left)),
                                   "_members": {curr["label"]: sorted(curr["members"])}})
                elif curr["qualified"]:
                    curr["source"] = "分散自 " + curr["qualified"][0][1]
                elif curr["best_label"] and curr["best_j"] > 0:
                    curr["source"] = "新生（最接近 %s，J=%.2f）" % (curr["best_label"], curr["best_j"])
                    events.append({"窗口对": pair_label, "事件": "新生",
                                   "涉及": curr["label"],
                                   "说明": "%s 出现；与历史群体 %s 的 Jaccard 仅 %.2f，低于阈值 %.1f，启用新标签"
                                           % (curr["label"], curr["best_label"], curr["best_j"], MATCH_THRESHOLD),
                                   "_members": {curr["label"]: sorted(curr["members"])}})
                else:
                    curr["source"] = "新生"
                    events.append({"窗口对": pair_label, "事件": "新生", "涉及": curr["label"],
                                   "说明": "%s 出现，与历史群体无重叠" % curr["label"],
                                   "_members": {curr["label"]: sorted(curr["members"])}})
            for prev in previous:
                successors = [curr for curr in current
                              if any(label == prev["label"] for _, label in curr["qualified"])]
                if not successors:
                    events.append({"窗口对": pair_label, "事件": "消散", "涉及": prev["label"],
                                   "说明": "%s 在 %s 窗口没有可续接的群体" % (prev["label"], window["label"]),
                                   "_members": {prev["label"]: sorted(prev["members"])}})
                elif len(successors) >= 2:
                    labels = [curr["label"] for curr in successors]
                    events.append({"窗口对": pair_label, "事件": "分散",
                                   "涉及": prev["label"] + "→" + "+".join(labels),
                                   "说明": "%s 分散为 %s" % (prev["label"], "、".join(labels)),
                                   "_members": {curr["label"]: sorted(curr["members"]) for curr in successors}})
        else:
            for curr in current:
                curr["source"] = "初始"
        for curr in current:
            curr.pop("qualified", None)
        window["labels"] = current
        previous = [{"label": curr["label"], "members": curr["members"]} for curr in current]
    for index, window in enumerate(windows):
        for curr in window["labels"]:
            if index + 1 == len(windows):
                curr["target"] = "末窗"
                continue
            successors = [nxt for nxt in windows[index + 1]["labels"]
                          if len(curr["members"] & nxt["members"]) /
                          len(curr["members"] | nxt["members"]) >= MATCH_THRESHOLD]
            if not successors:
                curr["target"] = "消散"
            elif len(successors) >= 2:
                curr["target"] = "分散为 " + "+".join(nxt["label"] for nxt in successors)
            elif successors[0]["label"] == curr["label"]:
                curr["target"] = "延续为 " + curr["label"]
            else:
                curr["target"] = "并入 " + successors[0]["label"]
    return events


def _author_status(window):
    status = {}
    for curr in window["labels"]:
        for author in curr["members"]:
            status[author] = curr["label"]
    for author in window["ungrouped"]:
        status[author] = "未成群"
    for author in window["isolated"]:
        status[author] = "无连边"
    return status


def _flows(windows, names):
    rows = []
    for index in range(len(windows) - 1):
        first, second = windows[index], windows[index + 1]
        pair = "%s→%s" % (first["label"], second["label"])
        before, after = _author_status(first), _author_status(second)
        for author in sorted(set(before) & set(after)):
            if before[author] == after[author]:
                continue
            starts_c = before[author].startswith("C")
            ends_c = after[author].startswith("C")
            kind = "群体间流动" if starts_c and ends_c else ("进入群体" if ends_c else "离开群体")
            rows.append({"窗口对": pair, "作者": author, "姓名": names.get(author, author),
                         "类型": kind, "从": before[author], "到": after[author]})
        for author in sorted(set(after) - set(before)):
            rows.append({"窗口对": pair, "作者": author, "姓名": names.get(author, author),
                         "类型": "新活跃", "从": "—", "到": after[author]})
        for author in sorted(set(before) - set(after)):
            rows.append({"窗口对": pair, "作者": author, "姓名": names.get(author, author),
                         "类型": "沉寂", "从": before[author], "到": "—"})
        for author in sorted(set(first["inst_map"]) | set(second["inst_map"])):
            old = first["inst_map"].get(author, set())
            new = second["inst_map"].get(author, set())
            if old != new:
                rows.append({"窗口对": pair, "作者": author, "姓名": names.get(author, author),
                             "类型": "机构变动",
                             "从": "/".join(sorted(old)) or "无记录",
                             "到": "/".join(sorted(new)) or "无记录"})
    return rows


def _index_series(values):
    base = values[0] if values else None
    if not base:
        return [None for _ in values]
    return [round(100 * value / base, 2) for value in values]


def compare(data, selection):
    window_size = _parse_window(selection.get("window", "1"))
    kind = _parse_weighting(selection.get("weighting", "分摊"))
    samples = _parse_samples(selection.get("null", "200"))
    years = sorted({work["year"] for work in data["works"]})
    if not years:
        raise ValueError("数据中没有作品")
    names = {author["author_id"]: author.get("display_name", author["author_id"])
             for author in data["authors"]}
    affiliations = defaultdict(list)
    for record in data["affiliations"]:
        affiliations[record["author_id"]].append(record)
    windows = []
    start = years[0]
    while start <= years[-1]:
        window_years = list(range(start, min(start + window_size, years[-1] + 1)))
        works = [work for work in data["works"] if start <= work["year"] < start + window_size]
        if works:
            windows.append(_analyze_window(_window_label(window_years), window_years,
                                           works, kind, affiliations, samples))
        start += window_size
    if not windows:
        raise ValueError("所选窗口内没有作品")
    if len(windows) > MAX_WINDOWS:
        raise ValueError("窗口数超过 %d，请增大窗口年数" % MAX_WINDOWS)
    for index, window in enumerate(windows):
        window["prev_label"] = windows[index - 1]["label"] if index else None
    events = _track_communities(windows)
    flows = _flows(windows, names)

    for window in windows:
        for curr in window["labels"]:
            curr["internal"], curr["support"] = _community_evidence(window["works"], curr["members"], kind)

    rows = []
    for window in windows:
        for curr in window["labels"]:
            members = sorted(curr["members"])
            rows.append({
                "窗口": window["label"],
                "群体": curr["label"],
                "规模": len(members),
                "来源": curr["source"],
                "去向": curr["target"],
                "内部权重": round(curr["internal"], 4),
                "支撑作品数": len(curr["support"]),
                "_detail": {
                    "窗口": window["label"],
                    "群体": curr["label"],
                    "成员": [{"author_id": author, "姓名": names.get(author, author),
                              "机构": sorted(window["inst_map"].get(author) or []) or ["无记录"]}
                             for author in members],
                    "支撑作品": curr["support"],
                    "成员变动": curr.get("membership_change"),
                    "历史重叠": ({"群体": curr["best_label"], "Jaccard": round(curr["best_j"], 4)}
                                 if curr["best_label"] and curr["best_j"] > 0 else None),
                },
            })

    event_rows = [{"窗口对": event["窗口对"], "事件": event["事件"],
                   "涉及": event["涉及"], "说明": event["说明"],
                   "_detail": {"涉及群体成员": event["_members"]}} for event in events]

    first, last = windows[0], windows[-1]

    def ratio(field):
        base = first[field]
        return _round(last[field] / base) if base else None

    def shift(field):
        if first[field] is None or last[field] is None:
            return None
        return round(100 * (last[field] - first[field]), 2)

    decomposition = [
        {"指标": "作品数", "首窗": first["work_count"], "末窗": last["work_count"],
         "变化": ratio("work_count"), "口径": "倍数"},
        {"指标": "平均团队规模", "首窗": _round(first["mean_size"]), "末窗": _round(last["mean_size"]),
         "变化": ratio("mean_size"), "口径": "倍数"},
        {"指标": "篇均协作权重", "首窗": _round(first["total_weight"] / first["work_count"]),
         "末窗": _round(last["total_weight"] / last["work_count"]),
         "变化": _round((last["total_weight"] / last["work_count"]) / (first["total_weight"] / first["work_count"]))
         if first["total_weight"] and last["work_count"] else None, "口径": "倍数"},
        {"指标": "总协作权重", "首窗": _round(first["total_weight"]), "末窗": _round(last["total_weight"]),
         "变化": ratio("total_weight"), "口径": "倍数=作品数×篇均权重"},
        {"指标": "跨群权重占比", "首窗": _round(first["cross_group_share"]),
         "末窗": _round(last["cross_group_share"]), "变化": shift("cross_group_share"), "口径": "百分点"},
        {"指标": "跨机构权重占比", "首窗": _round(first["cross_inst_share"]),
         "末窗": _round(last["cross_inst_share"]), "变化": shift("cross_inst_share"), "口径": "百分点"},
        {"指标": "活跃作者", "首窗": len(first["activity"]), "末窗": len(last["activity"]),
         "变化": len(last["activity"]) - len(first["activity"]), "口径": "人数"},
        {"指标": "群体数", "首窗": len(first["communities"]), "末窗": len(last["communities"]),
         "变化": len(last["communities"]) - len(first["communities"]), "口径": "个数"},
    ]

    null_rows = []
    for window in windows:
        if not window["null_metrics"]:
            continue
        for key, label in [("q", "模块度Q"), ("cross_group_share", "跨群权重占比"),
                           ("cross_inst_share", "跨机构权重占比")]:
            observed = window[key]
            values = [metrics[key] for metrics in window["null_metrics"] if metrics[key] is not None]
            if observed is None or not values:
                continue
            rank = round(100 * sum(1 for value in values if value <= observed) / len(values), 1)
            null_rows.append({"窗口": window["label"], "指标": label, "观察值": _round(observed),
                              "参照均值": _round(sum(values) / len(values)),
                              "参照P5": _round(_percentile(values, 0.05)),
                              "参照P95": _round(_percentile(values, 0.95)),
                              "观察值分位": rank})

    tables = []
    if event_rows:
        tables.append({"title": "结构事件", "rows": event_rows})
    if flows:
        tables.append({"title": "成员流动与机构变动", "rows": flows})
    tables.append({"title": "趋势分解（首窗→末窗）", "rows": decomposition})
    if null_rows:
        tables.append({"title": "与参照的比较", "rows": null_rows})

    largest = max(len(work["author_ids"]) for work in data["works"])
    moves = [row for row in flows if row["类型"] == "机构变动"]
    move_examples = "；".join("%s：%s→%s" % (row["作者"], row["从"], row["到"]) for row in moves[:3])
    coverage = "、".join("%s=%s" % (window["label"], _round(window["inst_coverage"]))
                         for window in windows)
    ungrouped = "、".join("%s=%d/%d" % (window["label"], len(window["ungrouped"]), len(window["isolated"]))
                          for window in windows)
    roles_works = sum(window["roles_works"] for window in windows)
    roles_assignments = sum(window["roles_assignments"] for window in windows)
    assignments = sum(window["assignments"] for window in windows)
    works_total = sum(window["work_count"] for window in windows)

    notes = [
        "贡献定义：%s。%s" % (WEIGHTING_NAMES[kind], WEIGHTING_NOTES[kind]),
        "本数据最大作品 %d 人：整对会计为 %d 对署名，分摊合计 %s，均摊合计 1；"
        "一篇多人作品不应默认折算为大量独立合作，可在左侧切换定义比较。"
        % (largest, largest * (largest - 1) // 2, _round(largest / 2, 2)),
        "时间窗口：非重叠连续窗口，每窗 %d 年，共 %d 窗（%s）；所用窗口与定义已随本结果保存。"
        % (window_size, len(windows), "、".join(window["label"] for window in windows)),
        "群体识别：在加权署名网络上做确定性模块度贪心分组，同一数据与定义结果可复现；"
        "群体是协作证据的聚类，不是行政划分。",
        "标签延续：相邻窗口成员 Jaccard ≥ %.1f 才沿用标签，否则启用新标签；"
        "成员变动大、无法可靠续接的群体不强行沿用旧名称。" % MATCH_THRESHOLD,
        "署名角色：%d/%d 条署名有角色记录（%d/%d 篇作品至少有一条）；缺失不代表无贡献，"
        "贡献定义不按角色加权。" % (roles_assignments, assignments, roles_works, works_total),
        "跨机构占比只统计窗口内两端机构均有生效记录的权重，各窗覆盖率：%s；历史机构按生效起止年匹配。"
        % coverage,
        ("机构变动：%d 名作者在相邻窗口间有机构记录变动（%s）；人员调动不等于新合作，明细见成员流动表。"
         % (len(moves), move_examples)) if moves else "机构变动：相邻窗口间没有机构记录变动。",
        "未进入群体的作者不计入任何群体（各窗 未成群/仅单人作品：%s）；单人作品共 %d 篇。"
        % (ungrouped, sum(window["solo"] for window in windows)),
    ]
    if samples and null_rows:
        high = ["%s·%s" % (row["窗口"], row["指标"]) for row in null_rows if row["观察值分位"] >= 95]
        low = ["%s·%s" % (row["窗口"], row["指标"]) for row in null_rows if row["观察值分位"] <= 5]
        inside = ["%s·%s" % (row["窗口"], row["指标"]) for row in null_rows if 5 < row["观察值分位"] < 95]
        notes.append("与保持作者活跃度和团队规模的参照（每窗 %d 个样本）相比：超出参照 P95 的指标：%s；"
                     "低于参照 P5 的指标：%s；落在参照 P5–P95 之间（偶然连边即可解释，不宜解读为组织变化）：%s。"
                     "分位只是对照线索，需结合作品与成员记录逐条核对。"
                     % (samples, "、".join(high) or "无", "、".join(low) or "无", "、".join(inside) or "无"))
    elif samples:
        notes.append("参照样本已请求（每窗 %d 个），但窗口内没有可比较的连边指标。" % samples)
    else:
        notes.append("参照样本数为 0：未生成偶然性对照，观察到的结构差异不能与随机连边区分。")
    failures = sum(window["null_failures"] for window in windows)
    if failures:
        notes.append("参照构造有 %d 个样本在尝试上限内未收敛，已跳过；参照统计基于其余样本。" % failures)
    notes.append("描述与参照分位用于复核线索，不替代研究设计或业务决定。")

    all_labels = {curr["label"] for window in windows for curr in window["labels"]}
    return {
        "heading": "结构对比：%d 个窗口 · %s" % (len(windows), WEIGHTING_NAMES[kind]),
        "summary": {
            "窗口": len(windows),
            "群体标签": len(all_labels),
            "结构事件": len(events),
            "末窗跨群权重占比": _round(last["cross_group_share"]),
            "末窗跨机构权重占比": _round(last["cross_inst_share"]),
            "参照样本/窗": samples,
        },
        "notes": notes,
        "definitions": {"窗口年数": window_size, "贡献定义": WEIGHTING_NAMES[kind],
                        "参照样本": samples, "群体延续阈值": MATCH_THRESHOLD},
        "windows": [window["label"] for window in windows],
        "xlabels": [window["label"] for window in windows],
        "trends": [
            {"label": "作品数（首窗=100）",
             "values": _index_series([window["work_count"] for window in windows])},
            {"label": "平均团队规模（首窗=100）",
             "values": _index_series([window["mean_size"] for window in windows])},
            {"label": "跨群权重占比（%）",
             "values": [_round(100 * window["cross_group_share"], 2)
                        if window["cross_group_share"] is not None else None for window in windows]},
            {"label": "跨机构权重占比（%）",
             "values": [_round(100 * window["cross_inst_share"], 2)
                        if window["cross_inst_share"] is not None else None for window in windows]},
        ],
        "rows": rows,
        "tables": tables,
    }
