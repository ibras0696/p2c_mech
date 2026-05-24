from app.integrations.platform_api.p2c_payments import extract_method_id, extract_payment_id


def test_extract_payment_id_from_data_object() -> None:
    payload = {"data": {"id": 3566992}}
    assert extract_payment_id(payload) == 3566992


def test_extract_method_id_prefers_direct_method() -> None:
    payload = {"method": "69eb8d7e6bdfddede1de9a79"}
    assert extract_method_id(payload) == "69eb8d7e6bdfddede1de9a79"
