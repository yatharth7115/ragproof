"""Copy authoritative, licensed resources into distributable packages."""

from pathlib import Path
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithResources(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        destination = Path(self.build_lib) / "ragproof_resources"
        for group in ("schemas", "fixtures"):
            self.copy_tree(str(root / group), str(destination / group))
        self.copy_file(str(root / "THIRD_PARTY_NOTICES.md"), str(destination))


setup(cmdclass={"build_py": BuildWithResources})
