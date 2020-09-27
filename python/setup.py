import setuptools
import warnings

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="telekinesis", 
    version="0.1.0-alpha.8",
    author="Telekinesis Cloud",
    author_email="support@telekinesis.cloud",
    description="A websockets based RPC to easily develop, deploy and call services online.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/telekinesis-cloud/telekinesis",
    packages=setuptools.find_packages(),
    install_requires=[
        'websockets',
        'cryptography',
        'makefun',
        'aiohttp',
        'bson',
        'ujson'
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
