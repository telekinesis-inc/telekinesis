import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="camarere-elias.j.neuman", # Replace with your own username
    version="0.0.1",
    author="Elias J Neuman",
    author_email="elias.j.neuman@gmail.com.com",
    description="A reverse-tunnel reverse-proxy to easily deploy web services.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/e-neuman/camarere",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
