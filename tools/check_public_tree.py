"""Check only the export tree; never traverse the original private workspace."""
import ast
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
ALLOWED_ROOT={"README.md","README.zh-CN.md","LICENSE",".gitignore","requirements-test.txt"}
ALLOWED_DIRS={"custom_components","examples","tests","docs","tools",".github"}
PATTERNS={
    "private IP":r"\b(?:192\.168\.|10\.\d+\.|172\.(?:1[6-9]|2\d|3[01])\.)\d+\.\d+\b",
    "JWT token":r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
    "GitHub token":r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
    "private key":r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
    "personal home path":r"(?:/home/[A-Za-z][\w-]+/|[A-Za-z]:[\\/]Users[\\/][^\s/\\]+[\\/])",
    "device identifier":r"\blight\.[a-z_]+\d{6,}[a-z0-9_]*",
    "email":r"\b[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}\b",
}


def public_files():
    for p in sorted(ROOT.rglob("*")):
        rel=p.relative_to(ROOT)
        if any(part in {".git","__pycache__",".venv",".pytest_cache"} for part in rel.parts):
            continue
        if p.is_file():
            if len(rel.parts)==1:
                assert rel.name in ALLOWED_ROOT,f"Unexpected root file: {rel}"
            else:
                assert rel.parts[0] in ALLOWED_DIRS,f"Unexpected directory: {rel}"
            assert p.suffix.lower() not in {".zip",".tgz",".log",".png",".jpg",".pyc"},f"Private artifact type: {rel}"
            assert p.name not in {"secrets.yaml",".env","calibration.json"},f"Local config: {rel}"
            yield p


def main():
    files=list(public_files())
    for p in files:
        content=p.read_text(encoding="utf-8")
        for label,pattern in PATTERNS.items():
            assert not re.search(pattern,content),f"Possible {label}: {p.relative_to(ROOT)}"
        if p.suffix==".py":
            ast.parse(content,filename=str(p))
        elif p.suffix==".json":
            json.loads(content)
        for target in re.findall(r"\]\(([^)]+)\)",content) if p.suffix==".md" else []:
            if "://" not in target and not target.startswith("#"):
                assert (p.parent/target.split("#")[0]).exists(),f"Broken local link: {target}"
    print(f"Public export checks passed: {len(files)} UTF-8 files; no private-pattern matches.")


if __name__=="__main__":
    main()
