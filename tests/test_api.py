from datetime import date

from tests.helpers import day_readings


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_post_readings_and_clean_day_has_no_anomalies(client):
    r = client.post("/readings", json={"readings": day_readings("M1", date(2026, 3, 28))})
    assert r.status_code == 200
    body = r.json()
    assert (body["accepted"], body["duplicates"], body["rejected_total"]) == (97, 0, 0)
    a = client.get("/anomalies", params={"date": "2026-03-28"}).json()
    assert a["expected_intervals"] == 96 and a["anomalies"] == []


def test_dst_days_have_92_and_100_intervals_and_no_false_gaps(client):
    for day, n in [("2026-03-29", 92), ("2026-10-25", 100)]:
        client.post("/readings", json={"readings": day_readings("M1", date.fromisoformat(day))})
        a = client.get("/anomalies", params={"date": day}).json()
        assert a["expected_intervals"] == n and a["anomalies"] == []


def test_naive_timestamps_are_rejected_per_row(client):
    good = day_readings("M1", date(2026, 10, 25))[:2]
    bad = {"meter_id": "M1", "ts": "2026-10-25T02:15:00", "register_kwh": 5.0}
    body = client.post("/readings", json={"readings": good + [bad]}).json()
    assert body["accepted"] == 2
    assert body["rejected"] == [{"index": 2, "reason": "naive_timestamp",
                                 "detail": "an explicit UTC offset is required"}]


def test_malformed_body_is_422(client):
    assert client.post("/readings", json={"readings": [{"meter_id": "M1"}]}).status_code == 422


def test_duplicate_and_conflict_are_reported(client):
    rows = day_readings("M1", date(2026, 3, 28))
    client.post("/readings", json={"readings": rows})
    changed = dict(rows[7], register_kwh=rows[7]["register_kwh"] + 1.0)
    body = client.post("/readings", json={"readings": [rows[3], changed]}).json()
    assert (body["accepted"], body["duplicates"], body["conflicts"]) == (0, 1, 1)
    kinds = {a["kind"] for a in client.get("/anomalies", params={"date": "2026-03-28"}).json()["anomalies"]}
    assert kinds == {"duplicate", "conflicting_duplicate"}


def test_unknown_meter_404_and_bad_kind_422(client):
    assert client.get("/anomalies", params={"date": "2026-03-28", "meter_id": "nope"}).status_code == 404
    assert client.get("/anomalies", params={"date": "2026-03-28", "kind": "bogus"}).status_code == 422


def test_reconciliation_flow(client):
    from app.timeutil import slot_starts
    from tests.helpers import BERLIN

    day = date(2026, 10, 25)
    client.post("/readings", json={"readings": day_readings("M1", day) + day_readings("M2", day)})
    points = [{"ts": s.astimezone(BERLIN).isoformat(), "kwh": 4.0} for s in slot_starts(day, BERLIN)]
    assert client.put("/grid-totals", json={"area": "DE", "points": points}).json()["upserted"] == 100
    out = client.get("/reconciliation", params={"date": "2026-10-25", "scale": 0.05}).json()
    assert out["compared_intervals"] == 100 and out["out_of_tolerance_intervals"] == 0
    bad = client.get("/reconciliation", params={"date": "2026-10-25", "scale": 0.03}).json()
    assert bad["out_of_tolerance_intervals"] == 100


def test_grid_totals_reject_naive_timestamps(client):
    r = client.put("/grid-totals", json={"area": "DE", "points": [{"ts": "2026-03-28T00:00:00", "kwh": 1.0}]})
    assert r.status_code == 422
