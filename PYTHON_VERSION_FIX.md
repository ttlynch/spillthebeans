# Python Version Requirement

## Issue

The SpillTheBeans bot requires **Python 3.10 or higher**, but your current version is **3.9.6**.

This is because `hyperliquid-python-sdk` uses Python 3.10+ type hint syntax (the `|` union operator), which is not available in Python 3.9.

## How to Check Your Python Version

```bash
python3 --version
```

## Solution: Upgrade to Python 3.10+

### Option 1: Using Homebrew (Recommended for macOS)

```bash
# Install Python 3.12 (latest stable)
brew install python@3.12

# Link it to make python3 point to 3.12
brew link --overwrite python@3.12

# Verify
python3 --version  # Should show 3.12.x
```

### Option 2: Using pyenv (Alternative)

```bash
# Install pyenv if you don't have it
brew install pyenv

# Add to your shell profile (~/.zshrc or ~/.bash_profile)
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init -)"' >> ~/.zshrc

# Reload your shell
source ~/.zshrc

# Install Python 3.12
pyenv install 3.12.0

# Set as global default
pyenv global 3.12.0

# Verify
python3 --version  # Should show 3.12.x
```

### Option 3: Using Conda

```bash
# Install Python 3.12
conda create -n spillthebeans python=3.12

# Activate the environment
conda activate spillthebeans

# Verify
python --version  # Should show 3.12.x
```

### Option 4: Official Installer

Download and install Python 3.12 from:
https://www.python.org/downloads/

After installation, you may need to update your PATH to use the new Python version.

## After Upgrading Python

1. **Reinstall dependencies** (important!):
   ```bash
   pip3 install -r requirements.txt
   ```

2. **Verify imports work**:
   ```bash
   python3 -c "import hyperliquid; import eth_account; import telegram; print('✅ All imports successful')"
   ```

3. **Run the bot**:
   ```bash
   python3 main.py
   ```

## Why This Requirement Exists

The `hyperliquid-python-sdk` uses modern Python type hints like this:

```python
def pool_details(self, pool_address: str, user_address: str | None = None) -> Json:
```

The `str | None` syntax (union operator) was introduced in **Python 3.10** via PEP 604. This is a cleaner alternative to `Optional[str]` or `Union[str, None]`.

## Alternative: Find Older SDK Version

If you cannot upgrade Python, you could try finding an older version of `hyperliquid-python-sdk` that supports Python 3.9:

```bash
# Search for compatible versions
pip3 search hyperliquid-python-sdk  # May not work with new pip
# Or check PyPI directly: https://pypi.org/project/hyperliquid-python-sdk/
```

However, this is not recommended as older versions may have bugs or lack features.

## Verification

After upgrading, verify everything works:

```bash
# Check Python version
python3 --version  # Should be 3.10+

# Test imports
python3 -c "from hyperliquid.info import Info; from hyperliquid.exchange import Exchange; print('✅ Hyperliquid SDK works')"

# Run bot
python3 main.py
```

## Troubleshooting

### "python3: command not found" after upgrade
You may need to update your shell's PATH:
```bash
# For zsh (macOS default)
echo 'export PATH="/opt/homebrew/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# For bash
echo 'export PATH="/opt/homebrew/bin:$PATH"' >> ~/.bash_profile
source ~/.bash_profile
```

### Still seeing Python 3.9
Check which Python is being used:
```bash
which python3
```

If it's still pointing to the old version, you may need to:
- Restart your terminal
- Run `hash -r` to clear the command cache
- Unlink the old Python version

### pip still using old Python
Use `python3 -m pip` instead of `pip3`:
```bash
python3 -m pip install -r requirements.txt
```
