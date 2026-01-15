from setuptools import setup, find_packages

setup(
    name="git-pulsar",
    version="0.1.0",
    description="Automated paranoid git backup for students",
    author="Jackson Ferguson",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "git-pulsar=src.cli:main",
            "git-pulsar-daemon=src.daemon:main",
        ],
    },
    python_requires=">=3.12",
)
