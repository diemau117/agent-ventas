"""Fixtures compartidos para tests."""
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def ensure_temp_dir():
    """Crea el directorio temporal para tests si no existe.

    Algunos tests usan sqlite:////tmp/opencode/agent_test.db pero el directorio
    /tmp/opencode/ puede no existir en entornos limpios. Este fixture lo crea
    automáticamente antes de cada test.
    """
    os.makedirs("/tmp/opencode", exist_ok=True)
    # Limpiar archivo de BD de tests si existe
    db_path = "/tmp/opencode/agent_test.db"
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass
    yield
