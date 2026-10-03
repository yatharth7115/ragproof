"""Build the tracked Stage 1 fixture from the official CUAD v1 archive."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PATH = ROOT / "data" / "raw" / "cuad" / "data.zip"
OUTPUT_PATH = ROOT / "fixtures" / "cuad_eval_subset.json"
SOURCE_URL = "https://raw.githubusercontent.com/TheAtticusProject/cuad/main/data.zip"
ARCHIVE_SHA256 = "f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a"

SELECTED_TITLES = (
    "CENTRACKINTERNATIONALINC_10_29_1999-EX-10.3-WEB SITE HOSTING AGREEMENT",
    "LIMEENERGYCO_09_09_1999-EX-10-DISTRIBUTOR AGREEMENT",
    "FTENETWORKS,INC_02_18_2016-EX-99.4-STRATEGIC ALLIANCE AGREEMENT",
)

PRIMARY_TITLE = SELECTED_TITLES[0]
PRIMARY_LABEL = "Notice Period To Terminate Renewal"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_archive() -> bytes:
    if ARCHIVE_PATH.exists():
        archive = ARCHIVE_PATH.read_bytes()
    else:
        ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            archive = response.read()
        ARCHIVE_PATH.write_bytes(archive)

    actual_hash = sha256(archive)
    if actual_hash != ARCHIVE_SHA256:
        raise RuntimeError(
            f"CUAD archive hash mismatch: expected {ARCHIVE_SHA256}, got {actual_hash}"
        )
    return archive


def main() -> None:
    archive = load_archive()
    with zipfile.ZipFile(io.BytesIO(archive)) as dataset_zip:
        dataset = json.loads(dataset_zip.read("CUADv1.json"))

    selected = []
    for record in dataset["data"]:
        if record["title"] not in SELECTED_TITLES:
            continue
        paragraph = record["paragraphs"][0]
        selected.append(
            {
                "title": record["title"],
                "context": paragraph["context"],
                "qas": paragraph["qas"],
            }
        )

    selected.sort(key=lambda record: SELECTED_TITLES.index(record["title"]))
    if [record["title"] for record in selected] != list(SELECTED_TITLES):
        raise RuntimeError("One or more pinned CUAD contracts were not found")

    primary = selected[0]
    matching_qas = [
        qa
        for qa in primary["qas"]
        if qa["id"].endswith(f"__{PRIMARY_LABEL}")
    ]
    matching_answers = [
        (index, answer)
        for index, answer in enumerate(matching_qas[0]["answers"])
        if "days" in answer["text"].lower()
    ] if len(matching_qas) == 1 else []
    if len(matching_qas) != 1 or len(matching_answers) != 1:
        raise RuntimeError("Pinned primary CUAD annotation changed unexpectedly")

    payload = {
        "provenance": {
            "dataset": "Contract Understanding Atticus Dataset (CUAD) v1",
            "publisher": "The Atticus Project",
            "license": "CC BY 4.0",
            "source_url": SOURCE_URL,
            "archive_sha256": ARCHIVE_SHA256,
            "transformation": "Deterministic three-contract subset; text and annotations unchanged"
        },
        "primary_case": {
            "title": PRIMARY_TITLE,
            "label": PRIMARY_LABEL,
            "qa_id": matching_qas[0]["id"],
            "answer_index": matching_answers[0][0]
        },
        "contracts": selected,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
