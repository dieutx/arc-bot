from __future__ import annotations

from setuptools import find_packages, setup


setup(
    name="arc-bot",
    version="0.2.0",
    description="Playwright automation for Arc Network daily tasks.",
    packages=find_packages("src"),
    package_dir={"": "src"},
    install_requires=[
        "playwright>=1.40,<2.0",
        "python-socks>=2.4,<3.0",
    ],
    entry_points={
        "console_scripts": [
            "arc-bot=arc_bot.cli:main_cli",
        ]
    },
)
