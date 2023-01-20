import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="telekinesis",
    version="0.1.62",
    author="Telekinesis, Inc.",
    author_email="support@telekinesis.cloud",
    description="Open Source, End-to-End Encrypted, Anywhere-to-Anywhere, Remote Procedure Calls.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/telekinesis-cloud/telekinesis",
    packages=setuptools.find_packages(),
    install_requires=["websockets", "cryptography", "bson", "ujson", "packaging"],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)
