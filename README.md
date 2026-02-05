# FTL2 - Faster Than Light (Refactored)

A refactored version of the faster-than-light automation framework, rebuilt with modern Python patterns using dataclasses and composition for clean architecture that's portable to Go.

## 🎯 Project Goals

- Refactor procedural code to use dataclasses and composition
- Reduce function parameter counts from 6-11 to 1-2
- Implement strategy patterns for extensibility
- Prepare codebase for eventual Go port
- Maintain backward compatibility with FTL's core features

## 🏗️ Architecture Principles

- **Dataclasses over dictionaries** - Typed configuration objects
- **Composition over inheritance** - Build complex behavior from simple components
- **Strategy pattern** - Pluggable execution strategies
- **Builder pattern** - Fluent APIs for complex object construction
- **Explicit interfaces** - Clear contracts for Go portability

## 🚀 Installation

### Using uv (recommended)

```bash
# Install development dependencies
uv pip install -e ".[dev]"
```

### Using pip

```bash
# Install development dependencies
pip install -e ".[dev]"
```

## 🧪 Development

### Running Tests

```bash
# Run all tests with coverage
pytest

# Run specific test file
pytest tests/test_example.py

# Run with verbose output
pytest -v
```

### Code Quality

```bash
# Format code with ruff
ruff format .

# Lint code
ruff check .

# Fix auto-fixable issues
ruff check --fix .

# Type checking with mypy
mypy src/ftl2
```

## 📊 Project Status

This is an active refactoring project. The codebase is being incrementally migrated from the original faster-than-light framework using the shared understanding methodology documented in the faster-than-light-refactor repository.

## 📝 License

Apache-2.0

## 🙏 Acknowledgments

Based on the original [faster-than-light](https://github.com/benthomasson/faster-than-light) automation framework.
