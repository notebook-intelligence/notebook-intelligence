---
apply: always
scope:
  file_patterns:
    - "*.py"
    - "*.ipynb"
active: true
priority: 1
---
# Security Guidelines
- Never hardcode credentials or API keys
- Validate all user inputs
- Use secure random generators for tokens