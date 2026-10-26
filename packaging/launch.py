"""Entry point of the packaged app (PyInstaller needs a script, not a module path)."""

from scan2form.ui.app import main

raise SystemExit(main())
