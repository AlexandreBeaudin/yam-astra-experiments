"""Read-only filename/calibration audit. Does not unpickle or contact hardware."""

import argparse
import json
from pathlib import Path


def index_files(directory, suffix):
    paths = list(directory.glob(f"*{suffix}")) if directory.is_dir() else []
    ids = {}
    invalid = []
    for path in paths:
        try:
            frame = int(path.stem)
        except ValueError:
            invalid.append(path.name)
            continue
        ids.setdefault(frame, []).append(path.name)
    return set(ids), {
        "exists": directory.is_dir(),
        "file_count": len(paths),
        "numeric_frame_count": len(ids),
        "invalid_names": sorted(invalid),
        "duplicate_numeric_ids": {str(k): v for k, v in ids.items() if len(v) > 1},
    }


def inspect(config, root):
    episode = root / config["episode_path"]
    obs_ids, obs_info = index_files(episode / config["observation_dir"], ".pkl")
    streams = {}
    common = set(obs_ids)
    for camera in config["cameras"]:
        ids, info = index_files(episode / camera["image_dir"], ".jpg")
        info["missing_observation_frames"] = sorted(obs_ids - ids)
        info["frames_without_observation"] = sorted(ids - obs_ids)
        info["intrinsics_exists"] = (root / camera["intrinsics"]).is_file()
        streams[camera["id"]] = info
        common &= ids
    calibrations = []
    for relative in config["calibration_files"]:
        path = root / relative
        entry = {"path": relative, "exists": path.is_file()}
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                entry["stable"] = payload.get("stable")
                entry["rmse_translation_mm"] = payload.get("rmse_translation_mm")
                entry["note"] = "null stable means no verdict provided, not accepted"
            except (ValueError, AttributeError) as exc:
                entry["error"] = str(exc)
        calibrations.append(entry)
    model = config.get("robot_model")
    if config.get("robot_model_project_relative"):
        model = Path(__file__).resolve().parents[1] / config["robot_model_project_relative"]
    return {
        "audit_scope": "filenames and saved calibration flags only",
        "episode": str(episode.resolve()),
        "observations": obs_info,
        "cameras": streams,
        "common_frame_count": len(common),
        "calibrations": calibrations,
        "robot_model_exists": bool(model and (root / model).is_file()),
        "timing": config["timing"],
        "backend": config["backend"],
        "ready_for_replay": False,
        "unverified": [
            "observation contents, image decoding and exposure timestamps",
            "robot model validity and joint/frame mappings",
            "calibration matrix validity and independent accuracy",
            "task assets, object trajectories and backend implementation",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        report = inspect(config, args.source_root.resolve())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
