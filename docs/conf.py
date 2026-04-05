# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

import os
import sys
sys.path.insert(0, os.path.abspath('../src'))  # Adjust based on where your code is

project = 'NFS'
copyright = '2026, Tom Kamphuys'
author = 'Tom Kamphuys'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx_autodoc_typehints',
    'sphinx.ext.githubpages',
    'sphinxcontrib.mermaid',
]

intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'numpy': ('https://numpy.org/doc/stable/', None),
    'scipy': ('https://docs.scipy.org/doc/scipy/', None),
}

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']



# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']

# -- Options for Mermaid -----------------------------------------------------
# https://sphinxcontrib-mermaid.readthedocs.io/en/latest/

mermaid_init_js = """
mermaid.initialize({
    startOnLoad: true,
    theme: 'base',
    themeVariables: {
        'primaryColor': '#e3f2fd',
        'primaryTextColor': '#2980b9',
        'primaryBorderColor': '#2980b9',
        'lineColor': '#2980b9',
        'secondaryColor': '#f1f8ff',
        'tertiaryColor': '#ffffff',
        'actorBkg': '#e3f2fd',
        'actorBorder': '#2980b9',
        'actorTextColor': '#2980b9',
        'actorLineColor': '#2980b9',
        'signalColor': '#2980b9',
        'signalTextColor': '#2980b9',
        'labelBoxBkgColor': '#e3f2fd',
        'labelBoxBorderColor': '#2980b9',
        'labelTextColor': '#2980b9',
        'loopTextColor': '#2980b9',
        'noteBkgColor': '#fff9c4',
        'noteBorderColor': '#fbc02d',
        'noteTextColor': '#333333',
        'actorLineColor': '#2980b9'
    },
    sequence: {
        diagramMarginX: 50,
        diagramMarginY: 10,
        actorMargin: 50,
        width: 150,
        height: 65,
        boxMargin: 10,
        boxTextMargin: 5,
        noteMargin: 10,
        messageMargin: 35,
        mirrorActors: true,
        bottomMarginLR: 10,
        useMaxWidth: true,
        rightAngles: false,
        showSequenceNumbers: false
    }
});
"""
