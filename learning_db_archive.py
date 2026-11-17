"""Safe chunked SQLite backup for the GTA6 GitHub learning workflow.

Never deletes or modifies the original learning.db. All saved chunks are
<= 24 MiB; restore checks SHA256 and SQLite integrity before replacing DB.
"""
import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

DB=Path("data/learning.db")
ARCHIVE=Path("data/learning_archive")
CHUNK=24*1024*1024

def sha(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
    return h.hexdigest()

def validate(path):
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0]!="ok":
            raise RuntimeError("SQLite integrity check failed")
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"snapshots","tokens","outcomes","runs"}.issubset(tables):
            raise RuntimeError("Incomplete learning DB; refusing to overwrite")

def unpack(archive,manifest,target):
    chunks=manifest.get("chunks",[])
    if manifest.get("format")!="learning-gzip-chunks-v1" or not chunks:
        raise RuntimeError("Invalid learning DB archive manifest")
    class Reader:
        def __init__(self):self.index=0;self.f=None
        def read(self,n=-1):
            if n<0:raise ValueError("Bounded read required")
            data=bytearray()
            while len(data)<n:
                if self.f is None:
                    if self.index==len(chunks):break
                    item=chunks[self.index]
                    name=f"chunk-{self.index:04d}.gzpart"
                    if item["name"]!=name:raise RuntimeError("Invalid chunk name")
                    path=archive/name
                    if not path.is_file() or path.stat().st_size!=item["size"] or sha(path)!=item["sha256"]:
                        raise RuntimeError("Missing or corrupted chunk: "+name)
                    self.f=open(path,"rb");self.index+=1
                part=self.f.read(n-len(data))
                if part:data.extend(part)
                else:self.f.close();self.f=None
            return bytes(data)
        def close(self):
            if self.f:self.f.close()
    reader=Reader()
    try:
        with gzip.GzipFile(fileobj=reader,mode="rb") as g,open(target,"wb") as f:
            for block in iter(lambda:g.read(1024*1024),b""):f.write(block)
    finally:reader.close()
    if target.stat().st_size!=manifest["db_size"] or sha(target)!=manifest["db_sha256"]:
        raise RuntimeError("Archive round-trip hash mismatch")
    validate(target)

def snapshot(db_path=DB,archive=ARCHIVE):
    db_path=Path(db_path).resolve();archive=Path(archive).resolve()
    if not db_path.is_file():raise FileNotFoundError("Missing learning DB; refusing new empty DB")
    validate(db_path)
    archive.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="learning-pack-",dir=archive.parent) as td:
        tmp=Path(td);backup=tmp/"snapshot.sqlite";compressed=tmp/"backup.gz"
        with sqlite3.connect(db_path.as_uri()+"?mode=ro",uri=True) as src:
            with sqlite3.connect(backup) as dst:src.backup(dst)
        validate(backup)
        with open(backup,"rb") as f,gzip.open(compressed,"wb",compresslevel=6) as out:
            for block in iter(lambda:f.read(1024*1024),b""):out.write(block)
        parts=[]
        with open(compressed,"rb") as f:
            while True:
                block=f.read(CHUNK)
                if not block:break
                name=f"chunk-{len(parts):04d}.gzpart"
                part=tmp/name;part.write_bytes(block)
                parts.append({"name":name,"size":len(block),"sha256":sha(part)})
        if not parts:raise RuntimeError("No compressed chunks produced")
        manifest={"format":"learning-gzip-chunks-v1","db_size":backup.stat().st_size,
                  "db_sha256":sha(backup),"chunks":parts}
        # Check a full restore before publishing the new manifest.
        unpack(tmp,manifest,tmp/"verified.sqlite")
        archive.mkdir(parents=True,exist_ok=True)
        for part in parts:os.replace(tmp/part["name"],archive/part["name"])
        check=tmp/"manifest.json"
        check.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
        os.replace(check,archive/"manifest.json")
        for old in archive.glob("chunk-*.gzpart"):
            if old.name not in {p["name"] for p in parts}:old.unlink()
        return {"status":"SNAPSHOT_VERIFIED","db_bytes":manifest["db_size"],
                "chunks":len(parts),"compressed_bytes":compressed.stat().st_size}

def restore(db_path=DB,archive=ARCHIVE):
    db_path=Path(db_path).resolve();archive=Path(archive).resolve()
    manifest_path=archive/"manifest.json"
    if not manifest_path.is_file():
        if not db_path.is_file():raise FileNotFoundError("No learning DB or archive")
        validate(db_path)
        return {"status":"LEGACY_DATABASE_VERIFIED"}
    manifest=json.loads(manifest_path.read_text())
    db_path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="learning-unpack-",dir=db_path.parent) as td:
        target=Path(td)/"restored.sqlite"
        unpack(archive,manifest,target)
        os.replace(target,db_path)
    return {"status":"DATABASE_RESTORED","bytes":db_path.stat().st_size}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("snapshot","restore"))
    a=p.parse_args()
    print(json.dumps(snapshot() if a.action=="snapshot" else restore()))
