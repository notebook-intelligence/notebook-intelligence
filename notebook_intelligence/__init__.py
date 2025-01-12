# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>
 
try:
    from ._version import __version__
except ImportError:
    # Fallback when using the package in dev mode without installing
    # in editable mode with pip. It is highly recommended to install
    # the package from a stable release or in editable mode: https://pip.pypa.io/en/stable/topics/local-project-installs/#editable-installs
    import warnings
    warnings.warn("Importing 'notebook_intelligence' outside a proper installation.")
    __version__ = "dev"
from .handlers import NotebookIntelligenceJupyterExtApp
from .extension import *

def _jupyter_labextension_paths():
    return [{
        "src": "labextension",
        "dest": "@mbektas/notebook-intelligence"
    }]


def _jupyter_server_extension_points():
    return [{
        "module": "notebook_intelligence",
        "app": NotebookIntelligenceJupyterExtApp
    }]
