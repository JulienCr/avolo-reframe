"""Scene-collection policy on top of the ObsWs transport: switch, create, guard.

Not adapter code: this prints, exits, and enforces the stream/record safety
policy, while adapters/obsws.py stays a thin transport client.
"""

from adapters.obsws import ObsWs

_COLLECTION_SWITCH_TIMEOUT_S = 30.0


def ensure_scene_collection(obs: ObsWs, collection_name: str) -> None:
    """Switch OBS to collection_name, creating it if absent; refuses while live.

    A heavy production collection can take longer than the client's default
    request timeout to finish switching, so this uses its own longer one.
    """
    current = obs.request("GetSceneCollectionList")
    previous = current["currentSceneCollectionName"]
    if previous == collection_name:
        print(f"Collection de scènes déjà active : {collection_name}")
        return

    stream_active = obs.request("GetStreamStatus")["outputActive"]
    record_active = obs.request("GetRecordStatus")["outputActive"]
    if stream_active or record_active:
        print(
            f"Refus de changer de collection de scènes ({previous} -> {collection_name}) : "
            "un stream ou un enregistrement est en cours."
        )
        raise SystemExit(1)

    exists = collection_name in current["sceneCollections"]
    request_type = "SetCurrentSceneCollection" if exists else "CreateSceneCollection"
    obs.request(request_type, {"sceneCollectionName": collection_name}, timeout=_COLLECTION_SWITCH_TIMEOUT_S)
    print(f"Collection de scènes : {previous} -> {collection_name}")
