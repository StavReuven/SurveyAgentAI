def create_campaign(client):
    payload = {
        "name": "Spring Research",
        "language": "en",
        "timezone": "UTC",
        "consent_text": "This call may be recorded for research.",
    }
    response = client.post("/api/campaigns", json=payload)
    assert response.status_code == 200
    return response.json()


def create_question(client, campaign_id, key, prompt, question_type="free_text", config=None):
    payload = {
        "key": key,
        "prompt": prompt,
        "question_type": question_type,
        "required": True,
        "config": config or {},
    }
    response = client.post(f"/api/campaigns/{campaign_id}/questions", json=payload)
    assert response.status_code == 200
    return response.json()


def test_index_and_health(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Survey Management (Campaign Builder)" in response.text

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "campaign-builder"}


def test_campaign_crud_and_summary(client):
    campaign = create_campaign(client)
    campaign_id = campaign["id"]

    response = client.get("/api/campaigns")
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = client.get("/api/campaigns/summary")
    assert response.status_code == 200
    summary = response.json()[0]
    assert summary["id"] == campaign_id
    assert summary["question_count"] == 0
    assert summary["participant_count"] == 0

    response = client.get(f"/api/campaigns/{campaign_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Spring Research"

    response = client.put(
        f"/api/campaigns/{campaign_id}",
        json={"name": "Spring Research Updated", "status": "active"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Spring Research Updated"
    assert updated["status"] == "active"

    response = client.delete(f"/api/campaigns/{campaign_id}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_campaign_quick_actions_duplicate_start_pause_resume_stop(client):
    campaign = create_campaign(client)
    campaign_id = campaign["id"]

    q1 = create_question(client, campaign_id, "q_nps", "Rate from 0 to 10", "rating")
    q2 = create_question(
        client,
        campaign_id,
        "q_reason",
        "Why did you choose that rating?",
        "free_text",
    )

    rule_payload = {
        "source_question_id": q1["id"],
        "operator": "lt",
        "value": "7",
        "action": "goto",
        "target_question_id": q2["id"],
        "priority": 10,
    }
    response = client.post(f"/api/campaigns/{campaign_id}/rules", json=rule_payload)
    assert response.status_code == 200

    response = client.post(f"/api/campaigns/{campaign_id}/duplicate")
    assert response.status_code == 200
    duplicate = response.json()
    assert duplicate["id"] != campaign_id
    assert duplicate["status"] == "draft"

    response = client.get(f"/api/campaigns/{campaign_id}/execution")
    assert response.status_code == 200
    assert response.json()["state"] == "idle"

    response = client.post(f"/api/campaigns/{campaign_id}/start")
    assert response.status_code == 200
    assert response.json()["state"] == "running"

    response = client.post(f"/api/campaigns/{campaign_id}/pause")
    assert response.status_code == 200
    assert response.json()["status"] == "paused"

    response = client.get(f"/api/campaigns/{campaign_id}/execution")
    assert response.status_code == 200
    assert response.json()["state"] == "paused"

    response = client.post(f"/api/campaigns/{campaign_id}/resume")
    assert response.status_code == 200
    assert response.json()["status"] == "active"

    response = client.post(f"/api/campaigns/{campaign_id}/stop")
    assert response.status_code == 200
    assert response.json()["state"] == "stopped"

    response = client.post(f"/api/campaigns/{campaign_id}/pause")
    assert response.status_code == 409


def test_calling_policy_get_and_update(client):
    campaign = create_campaign(client)
    campaign_id = campaign["id"]

    response = client.get(f"/api/campaigns/{campaign_id}/policy")
    assert response.status_code == 200
    default_policy = response.json()
    assert default_policy["window_start_hour"] == 9
    assert default_policy["window_end_hour"] == 18
    assert default_policy["max_attempts"] == 3

    update_payload = {
        "window_start_hour": 8,
        "window_end_hour": 20,
        "max_attempts": 4,
        "retry_delay_minutes": 15,
        "cooldown_hours": 2,
        "max_calls_per_minute": 25,
        "enabled": True,
    }
    response = client.put(f"/api/campaigns/{campaign_id}/policy", json=update_payload)
    assert response.status_code == 200
    updated = response.json()
    assert updated["window_start_hour"] == 8
    assert updated["window_end_hour"] == 20
    assert updated["max_attempts"] == 4
    assert updated["retry_delay_minutes"] == 15
    assert updated["cooldown_hours"] == 2
    assert updated["max_calls_per_minute"] == 25

    response = client.put(
        f"/api/campaigns/{campaign_id}/policy",
        json={
            "window_start_hour": 12,
            "window_end_hour": 10,
            "max_attempts": 3,
            "retry_delay_minutes": 30,
            "cooldown_hours": 1,
            "max_calls_per_minute": 10,
            "enabled": True,
        },
    )
    assert response.status_code == 422


def test_questions_crud_and_reorder(client):
    campaign = create_campaign(client)
    campaign_id = campaign["id"]

    q1 = create_question(client, campaign_id, "q_one", "Question one", "free_text")
    q2 = create_question(client, campaign_id, "q_two", "Question two", "mcq", {"options": ["A", "B"]})
    q3 = create_question(client, campaign_id, "q_three", "Question three", "rating")

    response = client.get(f"/api/campaigns/{campaign_id}/questions")
    assert response.status_code == 200
    listed = response.json()
    assert [item["id"] for item in listed] == [q1["id"], q2["id"], q3["id"]]

    response = client.put(
        f"/api/questions/{q2['id']}",
        json={"prompt": "Question two updated", "required": False},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["prompt"] == "Question two updated"
    assert updated["required"] is False

    new_order = [q3["id"], q1["id"], q2["id"]]
    response = client.post(
        f"/api/campaigns/{campaign_id}/questions/reorder",
        json={"question_ids": new_order},
    )
    assert response.status_code == 200
    assert response.json() == {"reordered": True}

    response = client.get(f"/api/campaigns/{campaign_id}/questions")
    assert response.status_code == 200
    reordered = response.json()
    assert [item["id"] for item in reordered] == new_order

    response = client.delete(f"/api/questions/{q1['id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_rules_crud(client):
    campaign = create_campaign(client)
    campaign_id = campaign["id"]

    source = create_question(client, campaign_id, "q_source", "Source question", "free_text")
    target = create_question(client, campaign_id, "q_target", "Target question", "free_text")

    payload = {
        "source_question_id": source["id"],
        "operator": "contains",
        "value": "help",
        "action": "goto",
        "target_question_id": target["id"],
        "priority": 5,
    }
    response = client.post(f"/api/campaigns/{campaign_id}/rules", json=payload)
    assert response.status_code == 200
    rule = response.json()

    response = client.get(f"/api/campaigns/{campaign_id}/rules")
    assert response.status_code == 200
    assert len(response.json()) == 1

    response = client.put(
        f"/api/rules/{rule['id']}",
        json={"value": "urgent", "priority": 1},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["value"] == "urgent"
    assert updated["priority"] == 1

    response = client.delete(f"/api/rules/{rule['id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_participant_upload_and_list(client):
    campaign = create_campaign(client)
    campaign_id = campaign["id"]

    csv_body = "\n".join(
        [
            "phone_number,full_name,locale",
            "+15550000001,Alex Doe,en-US",
            "+15550000002,Sam Lee,en-GB",
            "+15550000001,Duplicate,en-US",
        ]
    )

    response = client.post(
        f"/api/campaigns/{campaign_id}/participants/upload",
        files={"file": ("participants.csv", csv_body, "text/csv")},
    )
    assert response.status_code == 200
    upload_stats = response.json()
    assert upload_stats["inserted"] == 2
    assert upload_stats["skipped"] == 1

    response = client.get(f"/api/campaigns/{campaign_id}/participants")
    assert response.status_code == 200
    participants = response.json()
    assert len(participants) == 2
    phones = {item["phone_number"] for item in participants}
    assert phones == {"+15550000001", "+15550000002"}


def test_attempts_endpoint_empty(client):
    campaign = create_campaign(client)
    campaign_id = campaign["id"]

    response = client.get(f"/api/campaigns/{campaign_id}/attempts")
    assert response.status_code == 200
    assert response.json() == []
