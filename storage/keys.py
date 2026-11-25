from models.enums import AudioFormat


def edition_key(text_id: str, edition_id: str, version: str | None = None) -> str:
    path = f"base_texts/{text_id}/{edition_id}"
    return f"{path}.txt" if version is None else f"{path}/{version}.txt"


def recording_key(recording_id: str, audio_format: AudioFormat) -> str:
    return f"recordings/{recording_id}.{audio_format.value}"
