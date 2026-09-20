from scripts.director import actions_for_switch, camera_for_scene
from scripts.layout_lsa import CAMERAS


def test_camera_for_scene_maps_known_uuids_and_rejects_unknown() -> None:
    for cam in CAMERAS.values():
        assert camera_for_scene(cam.scene_uuid) == cam.key
    assert camera_for_scene("00000000-0000-0000-0000-000000000000") is None


def test_actions_for_switch_sends_one_live_and_no_live_to_the_rest() -> None:
    target = next(iter(CAMERAS))
    actions = actions_for_switch(target)
    by_action = {port: action for port, action in actions}

    assert by_action[CAMERAS[target].control_port] == "live"
    for key, cam in CAMERAS.items():
        if key != target:
            assert by_action[cam.control_port] == "no-live"


def test_actions_for_switch_ports_are_distinct_and_known() -> None:
    target = next(iter(CAMERAS))
    ports = [port for port, _ in actions_for_switch(target)]

    assert len(ports) == len(set(ports))
    assert set(ports) == {cam.control_port for cam in CAMERAS.values()}
