import setuptools
import warnings

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="telekinesis", 
    version="0.0.14",
    author="Elias J Neuman",
    author_email="elias.j.neuman@gmail.com.com",
    description="A websockets based RPC to easily develop, deploy and call services online.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/e-neuman/telekinesis",
    packages=setuptools.find_packages(),
    install_requires=[
        'websockets',
        'cryptography',
        'makefun'
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
