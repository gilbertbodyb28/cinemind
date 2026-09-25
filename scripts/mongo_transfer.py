"""Copy every collection of one MongoDB database into another, with indexes.

Used to move the Mac database into the NAS container (mongodump is not
installed on the Mac). Refuses to write into a target that already has data
unless --replace is given, so a second run cannot silently mix two copies.

  PYTHONPATH=.runtime/python python3 scripts/mongo_transfer.py \
      --source mongodb://localhost:27017 --target mongodb://192.168.50.94:27018 --db cinemind
"""
import argparse

from pymongo import MongoClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--db", default="cinemind")
    ap.add_argument("--replace", action="store_true")
    args = ap.parse_args()

    src = MongoClient(args.source)[args.db]
    dst = MongoClient(args.target)[args.db]
    existing = [name for name in dst.list_collection_names() if dst[name].estimated_document_count()]
    if existing and not args.replace:
        raise SystemExit(f"Target already has data in {existing}; rerun with --replace to overwrite.")

    for name in sorted(src.list_collection_names()):
        if name.startswith("system."):
            continue
        dst[name].drop()
        batch, copied = [], 0
        for doc in src[name].find({}, no_cursor_timeout=True):
            batch.append(doc)
            if len(batch) == 1000:
                dst[name].insert_many(batch, ordered=False)
                copied += len(batch)
                batch = []
        if batch:
            dst[name].insert_many(batch, ordered=False)
            copied += len(batch)
        for index_name, spec in src[name].index_information().items():
            if index_name == "_id_":
                continue
            keys = spec.pop("key")
            spec.pop("v", None)
            spec.pop("ns", None)
            dst[name].create_index(keys, name=index_name, **spec)
        source_count = src[name].count_documents({})
        target_count = dst[name].count_documents({})
        status = "OK" if source_count == target_count else "MISMATCH"
        print(f"{status:8} {name}: {source_count} -> {target_count}")


if __name__ == "__main__":
    main()
