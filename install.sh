#!/bin/bash

echo "🧹 Cleaning up old venv and starting fresh installation..."

# Deactivate if currently in venv
if [[ "$VIRTUAL_ENV" != "" ]]; then
    deactivate
fi

# Remove old venv
echo "🗑️ Removing old venv..."
rm -rf venv

# Create new venv with Python 3.12
echo "🏗️ Creating new venv with Python 3.12..."
/opt/homebrew/bin/python3.12 -m venv venv

# Activate venv
echo "✅ Activating venv..."
source venv/bin/activate

# Upgrade pip
echo "⬆️ Upgrading pip..."
pip install --upgrade pip

# Install requirements
echo "📦 Installing dependencies from requirements.txt..."
pip install -r requirements.txt

# Verify installation
echo ""
echo "🔍 Verifying installation..."

python -c "import pandas; print(f'✅ pandas {pandas.__version__}')"
python -c "import numpy; print(f'✅ numpy {numpy.__version__}')"
python -c "import matplotlib; print(f'✅ matplotlib {matplotlib.__version__}')"
python -c "import mplfinance; print(f'✅ mplfinance {mplfinance.__version__}')"
python -c "import httpx; print(f'✅ httpx {httpx.__version__}')"
python -c "import telegram; print(f'✅ python-telegram-bot installed')"
python -c "from dotenv import load_dotenv; print('✅ python-dotenv installed')"
python -c "from hyperliquid.info import Info; print('✅ hyperliquid-python-sdk installed')"
python -c "import eth_account; print('✅ eth-account installed')"

echo ""
echo "🎉 Installation complete! To start the bot:"
echo ""
echo "   cd $(pwd)"
echo "   source venv/bin/activate"
echo "   python main.py"
echo ""
