from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json
import random
import unittest

from collaboration_map.domain import explore
from collaboration_map.service import Workbench
from collaboration_map.server import make_server
from collaboration_map.structure import compare, _detect_communities, _draw_assignment


ROOT = Path(__file__).resolve().parents[1]


def load_fixture():
    return json.loads((ROOT / "fixtures" / "data.json").read_text())


def make_data(work_groups, moves=()):
    """work_groups: {year: [[author, ...], ...]}；moves: (author, 新机构, 起始年) 列表。"""
    authors = sorted({author for works in work_groups.values() for work in works for author in work})
    affiliations = [{"author_id": author, "institution": "school_a", "start": 2020, "end": 2030}
                    for author in authors]
    for author, institution, start in moves:
        affiliations = [record for record in affiliations
                        if not (record["author_id"] == author and record["end"] >= start)]
        affiliations.append({"author_id": author, "institution": "school_a", "start": 2020, "end": start - 1})
        affiliations.append({"author_id": author, "institution": institution, "start": start, "end": 2030})
    works = []
    for year, groups in work_groups.items():
        for index, group in enumerate(groups):
            works.append({"work_id": "w_%s_%d" % (year, index), "year": year,
                          "author_ids": list(group), "roles": {}})
    return {"authors": [{"author_id": author, "display_name": "姓名" + author} for author in authors],
            "affiliations": affiliations, "works": works}


def table_rows(view, title):
    for table in view["tables"]:
        if table["title"] == title:
            return table["rows"]
    return []


class DetectionTests(unittest.TestCase):
    def test_two_triangles_with_weak_bridge_split(self):
        adjacency = {
            "a": {"b": 1.0, "c": 1.0},
            "b": {"a": 1.0, "c": 1.0, "d": 0.01},
            "c": {"a": 1.0, "b": 1.0},
            "d": {"b": 0.01, "e": 1.0, "f": 1.0},
            "e": {"d": 1.0, "f": 1.0},
            "f": {"d": 1.0, "e": 1.0},
        }
        partition = _detect_communities(adjacency)
        self.assertEqual(partition["a"], partition["b"])
        self.assertEqual(partition["b"], partition["c"])
        self.assertEqual(partition["d"], partition["e"])
        self.assertEqual(partition["e"], partition["f"])
        self.assertNotEqual(partition["a"], partition["d"])

    def test_detection_is_deterministic(self):
        data = load_fixture()
        first = compare(data, {"window": "1", "weighting": "分摊", "null": "0"})
        second = compare(load_fixture(), {"window": "1", "weighting": "分摊", "null": "0"})
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


class WeightingTests(unittest.TestCase):
    def setUp(self):
        self.data = make_data({2023: [["a", "b", "c"], ["d", "e"]],
                               2024: [["a", "b", "c", "d"], ["d", "e"], ["e", "f"]]})

    def total(self, weighting):
        view = compare(self.data, {"window": "1", "weighting": weighting, "null": "0"})
        return {row["指标"]: row for row in table_rows(view, "趋势分解（首窗→末窗）")}["总协作权重"]

    def test_pair_weight_totals_differ_by_definition(self):
        pairs = self.total("整对")
        fractional = self.total("分摊")
        uniform = self.total("均摊")
        self.assertEqual(pairs["首窗"], 4)       # C(3,2) + C(2,2)
        self.assertEqual(fractional["首窗"], 2.5)  # 3/2 + 1
        self.assertEqual(uniform["首窗"], 2)      # 1 + 1
        self.assertGreater(pairs["首窗"], fractional["首窗"])

    def test_decomposition_identity(self):
        rows = {row["指标"]: row for row in table_rows(
            compare(self.data, {"window": "1", "weighting": "分摊", "null": "0"}), "趋势分解（首窗→末窗）")}
        works = rows["作品数"]["变化"]
        per_work = rows["篇均协作权重"]["变化"]
        total = rows["总协作权重"]["变化"]
        self.assertAlmostEqual(works * per_work, total, places=3)

    def test_mega_work_note_quantifies_pair_explosion(self):
        view = compare(load_fixture(), {"window": "1", "weighting": "分摊", "null": "0"})
        notes = "\n".join(view["notes"])
        self.assertIn("780", notes)
        self.assertIn("20.0", notes)


class TrackingTests(unittest.TestCase):
    def labels_by_window(self, view):
        result = {}
        for row in view["rows"]:
            result.setdefault(row["窗口"], {})[row["群体"]] = row
        return result

    def test_continuation_keeps_label_and_records_membership_change(self):
        data = make_data({2023: [["a", "b", "c", "d"]], 2024: [["a", "b", "c", "e"]]})
        view = compare(data, {"window": "1", "weighting": "分摊", "null": "0"})
        labels = self.labels_by_window(view)
        self.assertEqual(list(labels["2024"]), ["C1"])
        row = labels["2024"]["C1"]
        self.assertEqual(row["来源"], "延续自 C1")
        self.assertEqual(row["_detail"]["成员变动"], {"加入": ["e"], "离开": ["d"]})

    def test_unreliable_overlap_gets_new_label_not_forced_name(self):
        data = make_data({2023: [["a", "b", "c", "d"]], 2024: [["a", "b", "e", "f"]]})
        view = compare(data, {"window": "1", "weighting": "分摊", "null": "0"})
        labels = self.labels_by_window(view)
        self.assertEqual(list(labels["2023"]), ["C1"])
        self.assertNotEqual(list(labels["2024"]), ["C1"])
        new_row = next(iter(labels["2024"].values()))
        self.assertIn("新生", new_row["来源"])
        self.assertEqual(new_row["_detail"]["历史重叠"], {"群体": "C1", "Jaccard": round(2 / 6, 4)})
        events = {(row["事件"], row["涉及"]) for row in table_rows(view, "结构事件")}
        self.assertIn(("消散", "C1"), events)

    def test_merge_event_and_label_inheritance(self):
        data = make_data({2023: [["a", "b"], ["c", "d"]], 2024: [["a", "b", "c", "d"]]})
        view = compare(data, {"window": "1", "weighting": "分摊", "null": "0"})
        labels = self.labels_by_window(view)
        self.assertEqual(set(labels["2023"]), {"C1", "C2"})
        self.assertEqual(list(labels["2024"]), ["C1"])
        events = [row for row in table_rows(view, "结构事件") if row["事件"] == "合并"]
        self.assertEqual(len(events), 1)
        self.assertIn("C1", events[0]["涉及"])
        self.assertIn("C2", events[0]["涉及"])

    def test_split_event_spawns_new_label(self):
        data = make_data({2023: [["a", "b", "c", "d"]], 2024: [["a", "b"], ["c", "d"]]})
        view = compare(data, {"window": "1", "weighting": "分摊", "null": "0"})
        labels = self.labels_by_window(view)
        self.assertEqual(set(labels["2024"]), {"C1", "C2"})
        self.assertEqual(labels["2023"]["C1"]["去向"], "分散为 C1+C2")
        events = [row for row in table_rows(view, "结构事件") if row["事件"] == "分散"]
        self.assertEqual(len(events), 1)

    def test_institution_move_is_not_reported_as_collaboration(self):
        data = make_data({2023: [["a", "b", "c"]], 2024: [["a", "b", "c"]]},
                          moves=[("a", "school_b", 2024)])
        view = compare(data, {"window": "1", "weighting": "分摊", "null": "0"})
        flows = table_rows(view, "成员流动与机构变动")
        moves = [row for row in flows if row["类型"] == "机构变动"]
        self.assertEqual([(row["作者"], row["从"], row["到"]) for row in moves],
                         [("a", "school_a", "school_b")])
        self.assertFalse(any(row["类型"] == "群体间流动" and row["作者"] == "a" for row in flows))
        self.assertIn("人员调动不等于新合作", "\n".join(view["notes"]))


class NullModelTests(unittest.TestCase):
    def test_assignment_preserves_activity_and_team_sizes(self):
        sizes = [3, 2, 4, 1]
        activity = {"a": 2, "b": 1, "c": 2, "d": 1, "e": 2, "f": 1, "g": 1}
        rng = random.Random(7)
        for _ in range(30):
            assignment = _draw_assignment(sizes, activity, sorted(activity), rng)
            self.assertIsNotNone(assignment)
            self.assertEqual(sorted(len(group) for group in assignment), sorted(sizes))
            counts = {}
            for group in assignment:
                self.assertEqual(len(set(group)), len(group))
                for author in group:
                    counts[author] = counts.get(author, 0) + 1
            self.assertEqual(counts, dict(activity))

    def test_null_summary_present_with_percentiles(self):
        view = compare(load_fixture(), {"window": "1", "weighting": "分摊", "null": "20"})
        rows = table_rows(view, "与参照的比较")
        self.assertEqual(len(rows), 12)
        for row in rows:
            self.assertGreaterEqual(row["观察值分位"], 0)
            self.assertLessEqual(row["观察值分位"], 100)
            self.assertLessEqual(row["参照P5"], row["参照P95"])

    def test_zero_samples_disables_reference_with_note(self):
        view = compare(load_fixture(), {"window": "1", "weighting": "分摊", "null": "0"})
        self.assertEqual(table_rows(view, "与参照的比较"), [])
        self.assertIn("参照样本数为 0", "\n".join(view["notes"]))


class CompareIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.data = load_fixture()

    def test_fixture_view_is_serializable_and_traceable(self):
        view = compare(self.data, {"window": "1", "weighting": "分摊", "null": "10"})
        json.dumps(view, allow_nan=False)
        self.assertEqual(view["summary"]["窗口"], 4)
        self.assertTrue(view["rows"])
        for row in view["rows"]:
            detail = row["_detail"]
            self.assertTrue(detail["成员"])
            self.assertTrue(detail["支撑作品"])
            self.assertIn("author_id", detail["成员"][0])
            self.assertIn("work_id", detail["支撑作品"][0])

    def test_compare_does_not_mutate_inputs(self):
        before = deepcopy(self.data)
        compare(self.data, {"window": "1", "weighting": "分摊", "null": "10"})
        self.assertEqual(before, self.data)

    def test_fixture_has_institution_moves_in_flows(self):
        view = compare(self.data, {"window": "1", "weighting": "分摊", "null": "0"})
        moves = [row for row in table_rows(view, "成员流动与机构变动") if row["类型"] == "机构变动"]
        self.assertEqual(len(moves), 6)
        self.assertTrue(all(row["到"] == "school_new" for row in moves))

    def test_roles_and_coverage_in_notes(self):
        view = compare(self.data, {"window": "1", "weighting": "分摊", "null": "0"})
        notes = "\n".join(view["notes"])
        self.assertIn("署名角色", notes)
        self.assertIn("覆盖率", notes)

    def test_single_window_has_no_events(self):
        data = make_data({2023: [["a", "b"], ["c", "d"]]})
        view = compare(data, {"window": "1", "weighting": "分摊", "null": "0"})
        self.assertEqual(view["summary"]["窗口"], 1)
        self.assertEqual(table_rows(view, "结构事件"), [])
        for row in view["rows"]:
            self.assertEqual(row["来源"], "初始")
            self.assertEqual(row["去向"], "末窗")

    def test_window_size_two(self):
        view = compare(self.data, {"window": "2", "weighting": "分摊", "null": "0"})
        self.assertEqual(view["windows"], ["2023-2024", "2025-2026"])

    def test_invalid_parameters_rejected(self):
        with self.assertRaises(ValueError):
            compare(self.data, {"window": "9"})
        with self.assertRaises(ValueError):
            compare(self.data, {"weighting": "随机"})
        with self.assertRaises(ValueError):
            compare(self.data, {"null": "-1"})
        with self.assertRaises(ValueError):
            explore(self.data, {"view": "别的"})

    def test_explore_dispatches_views(self):
        year_view = explore(self.data, {"view": "年度名单", "year": "2025"})
        self.assertIn("series", year_view)
        structure_view = explore(self.data, {"view": "结构对比", "window": "1",
                                             "weighting": "分摊", "null": "0"})
        self.assertIn("trends", structure_view)


class ServiceStructureTests(unittest.TestCase):
    def test_saved_structure_view_is_reproducible_after_restart(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workbench = Workbench(ROOT, temporary.name)
        selection = {"view": "结构对比", "window": "1", "weighting": "分摊", "null": "10"}
        saved = workbench.save({"title": "结构复核", "selection": selection})
        self.assertEqual(saved["selection"], selection)
        restarted = Workbench(ROOT, temporary.name)
        self.assertEqual(saved, restarted.store.notebook(saved["id"]))


class HttpStructureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.server = make_server(ROOT, self.temporary.name, port=0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temporary.cleanup()

    def post(self, payload):
        request = Request(self.base + "/api/explore", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        return urlopen(request)

    def test_structure_view_over_http(self):
        with self.post({"selection": {"view": "结构对比", "window": "2",
                                      "weighting": "均摊", "null": "0"}}) as response:
            result = json.load(response)
        self.assertEqual(response.status, 200)
        self.assertIn("结构对比", result["view"]["heading"])
        self.assertEqual(result["selection"]["window"], "2")

    def test_invalid_weighting_returns_client_error(self):
        with self.assertRaises(HTTPError) as caught:
            self.post({"selection": {"view": "结构对比", "weighting": "错"}})
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()


if __name__ == "__main__":
    unittest.main()
