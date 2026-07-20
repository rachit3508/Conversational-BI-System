# CLAUDE.md
This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Description
The Conversational BI System is an end-to-end analytics platform that enables business users to interact with enterprise SQL Server databases through natural language. Each natural language question is automatically translated into a SQL query, executed against the selected database, and results are presented simultaneously as structured tabular data, the underlying SQL for transparency, an 
AI-generated natural language summary, and an interactive Plotly visualisation. The interface preserves full conversation history, enabling follow-up queries and multi-turn analysis sessions.

## Architecture

Conversational-BI-System/
├── CLAUDE.md
├── LICENSE
├── README.md
├── .gitignore
├── .python-version          # 3.11 (uv-managed)
├── pyproject.toml           # dependencies still empty
├── main.py                  # uv init placeholder
├── prompt.txt               # empty
├── logs/                    # gitignored; timestamped run logs
└── src/
    ├── __init__.py
    ├── exception/
    │   ├── __init__.py
    │   └── exception.py     # CustomException — traceback-aware, self-logging
    └── logging/
        ├── __init__.py
        └── logger.py        # configured `logger` (file + stdout)

## Rules
1. Always add the logging and custom exception modules functionaities in all the newly created python files or modules.
2. For every fearture or functionality create a separate python file. Try to make the project in modular coding.