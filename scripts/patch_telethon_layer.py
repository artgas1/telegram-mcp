#!/usr/bin/env python3
"""Пересобирает TL-классы Telethon под актуальный слой MTProto.

Зачем
-----
Релизы Telethon отстают от боевого Telegram. На 11.08.2026 последний релиз
1.44.0 (15.06.2026) собран под LAYER 227, а сервер уже отвечает объектами
LAYER 228, где сменился конструктор `user` (0x31774388 → 0xb1b8cc83).

Клиент, который не знает конструктор, не просто теряет одно поле — у него
"едет" разбор всего буфера. Наружу это выходит как `TypeNotFoundError` с
идентификатором, которого нет ни в одной схеме (мусор из рассинхрона), а на
уровне MCP — как невнятный `GEN-ERR-###`, неотличимый от «нет такого
пользователя». Ломаются `list_chats`, `get_common_chats`, `resolve_username`,
`get_full_user`; при этом соседние вызовы работают, что сбивает диагностику.

Что делает скрипт
-----------------
Берёт вендоренную схему (`vendor/api-layer228.tl`, снята из dev-ветки
Telegram Desktop) и вендоренный генератор Telethon, пересобирает из них
`telethon/tl/{types,functions,alltlobjects.py}` и кладёт поверх установленного
пакета. Код библиотеки (клиент, сеть, custom-классы) остаётся от релиза —
меняются только сгенерированные TL-классы.

Использование
-------------
    uv run python scripts/patch_telethon_layer.py            # применить
    uv run python scripts/patch_telethon_layer.py --check    # только проверить
    uv run python scripts/patch_telethon_layer.py --restore  # откатить из бэкапа

Бэкап оригинала кладётся рядом с пакетом (`tl.orig-layer<N>.tar.gz`) при первом
запуске, так что откат не требует переустановки.

⚠️ Уже запущенные процессы держат старый код в памяти — патч подхватят только
новые. После применения перезапусти Claude-сессии (или MCP-процессы).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR_SCHEMA = REPO / "vendor" / "api-layer228.tl"
VENDOR_GENERATOR = REPO / "vendor" / "telethon_generator"
GENERATED = ("types", "functions", "alltlobjects.py")


def telethon_tl_dir() -> Path:
    """Каталог tl/ установленного telethon — того, которым реально запускается MCP."""
    import telethon

    return Path(telethon.__file__).parent / "tl"


def current_layer() -> tuple[int, str]:
    """Слой установленного telethon — ОБЯЗАТЕЛЬНО в отдельном процессе.

    В своём процессе `telethon.tl` уже импортирован, и после подмены файлов
    Python отдаёт закешированный модуль: проверка показывала бы старый слой и
    рапортовала «патч не применился» на успешно применённом патче.
    """
    code = (
        "from telethon.tl.alltlobjects import LAYER;"
        "from telethon.tl.types import User;"
        "print(LAYER, hex(User.CONSTRUCTOR_ID))"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit(f"не смог прочитать слой telethon:\n{res.stderr}")
    layer, ctor = res.stdout.split()
    return int(layer), ctor


def schema_layer() -> int:
    for line in VENDOR_SCHEMA.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("// LAYER "):
            return int(line.strip().rsplit(" ", 1)[1])
    raise SystemExit(f"в {VENDOR_SCHEMA} нет строки '// LAYER N'")


def backup_path(tl_dir: Path, layer: int) -> Path:
    return tl_dir.parent / f"tl.orig-layer{layer}.tar.gz"


def do_check() -> int:
    layer, user_ctor = current_layer()
    want = schema_layer()
    print(f"установлено: LAYER {layer}, user# = {user_ctor}")
    print(f"вендоренная схема: LAYER {want}")
    if layer == want:
        print("✅ совпадает — патч применён")
        return 0
    print("❌ расходится — нужен патч (см. docstring)")
    return 1


def do_restore() -> int:
    tl_dir = telethon_tl_dir()
    backups = sorted(tl_dir.parent.glob("tl.orig-layer*.tar.gz"))
    if not backups:
        raise SystemExit(
            "бэкапа нет — откат через переустановку: uv sync --reinstall-package telethon"
        )
    archive = backups[-1]
    for name in GENERATED:
        target = tl_dir / name
        shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
    with tarfile.open(archive) as tar:
        # filter="data" — дефолт с Python 3.14; задаём явно, чтобы не ловить DeprecationWarning
        tar.extractall(tl_dir.parent, filter="data")
    _drop_pycache(tl_dir.parent)
    print(f"откат из {archive.name} выполнен")
    return do_check()


def _drop_pycache(root: Path) -> None:
    for cache in root.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def do_patch() -> int:
    if not VENDOR_SCHEMA.exists() or not VENDOR_GENERATOR.exists():
        raise SystemExit("нет vendor/api-layer228.tl или vendor/telethon_generator")

    tl_dir = telethon_tl_dir()
    layer_before, user_before = current_layer()
    want = schema_layer()
    if layer_before == want:
        print(f"уже LAYER {want} — делать нечего")
        return 0

    backup = backup_path(tl_dir, layer_before)
    if not backup.exists():
        with tarfile.open(backup, "w:gz") as tar:
            for name in GENERATED:
                tar.add(tl_dir / name, arcname=f"tl/{name}")
        print(f"бэкап оригинала: {backup}")

    # Генератор Telethon пишет рядом с собой, поэтому собираем во временном дереве.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copytree(VENDOR_GENERATOR, work / "telethon_generator")
        shutil.copy(VENDOR_SCHEMA, work / "telethon_generator" / "data" / "api.tl")
        (work / "telethon" / "tl").mkdir(parents=True, exist_ok=True)

        gen = work / "gen.py"
        gen.write_text(
            "import sys, itertools; sys.path.insert(0, '.')\n"
            "from pathlib import Path\n"
            "from telethon_generator.parsers import parse_tl, find_layer, parse_errors, parse_methods\n"
            "from telethon_generator.generators import generate_tlobjects\n"
            "gen = Path('telethon_generator')\n"
            "tls = sorted(gen.glob('data/*.tl'))\n"
            "layer = next(filter(None, map(find_layer, tls)))\n"
            "errors = list(parse_errors(gen / 'data/errors.csv'))\n"
            "methods = list(parse_methods(gen / 'data/methods.csv', gen / 'data/friendly.csv',\n"
            "                             {e.str_code: e for e in errors}))\n"
            "objs = list(itertools.chain(*(parse_tl(f, layer, methods) for f in tls)))\n"
            "generate_tlobjects(objs, layer, 2, Path('telethon/tl'))\n"
            "print('LAYER', layer)\n",
            encoding="utf-8",
        )
        res = subprocess.run([sys.executable, "gen.py"], cwd=work, capture_output=True, text=True)
        if res.returncode != 0:
            raise SystemExit(f"генерация упала:\n{res.stdout}\n{res.stderr}")

        built = work / "telethon" / "tl"
        missing = [n for n in GENERATED if not (built / n).exists()]
        if missing:
            raise SystemExit(f"генератор не создал: {missing}")

        for name in GENERATED:
            target = tl_dir / name
            shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
            src = built / name
            shutil.copytree(src, target) if src.is_dir() else shutil.copy(src, target)

    _drop_pycache(tl_dir.parent)
    print(f"было: LAYER {layer_before}, user# = {user_before}")
    return do_check()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="показать текущий и целевой слой")
    g.add_argument("--restore", action="store_true", help="откатить из бэкапа")
    args = ap.parse_args()
    if args.check:
        return do_check()
    if args.restore:
        return do_restore()
    return do_patch()


if __name__ == "__main__":
    raise SystemExit(main())
