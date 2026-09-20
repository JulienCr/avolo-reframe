from scripts.director import actions_for_switch, camera_for_scene, decide_switch, mirror_visibility_patches
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


def test_decide_switch_resolves_camera_mirror_and_unmapped_scene() -> None:
    cam = next(iter(CAMERAS.values()))
    mirror_names = frozenset({"Coming", "End"})

    assert decide_switch(cam.scene_uuid, cam.scene_name, mirror_names) == (cam.key, None)
    assert decide_switch("00000000-0000-0000-0000-000000000000", "Coming", mirror_names) == (None, "Coming")
    assert decide_switch("00000000-0000-0000-0000-000000000000", "brb", mirror_names) == (None, None)


def test_mirror_visibility_patches_shows_only_the_target() -> None:
    mirror_items = {"Coming": 10, "End": 11}

    assert set(mirror_visibility_patches(mirror_items, "Coming")) == {(10, True), (11, False)}
    assert set(mirror_visibility_patches(mirror_items, None)) == {(10, False), (11, False)}
