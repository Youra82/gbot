# tests/test_grid.py
"""
Basis-Tests fuer den gbot Grid-Trading-Bot.
"""
import os
import sys
import json
import pytest

# Pfad zum src-Verzeichnis hinzufuegen
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from gbot.strategy.grid_logic import (
    calculate_grid_levels,
    get_grid_spacing,
    calculate_amount_per_grid,
    split_levels_by_price,
    find_next_buy_level,
    find_next_sell_level,
    profit_per_cycle,
    price_in_range,
)


# ---------------------------------------------------------------------------
# grid_logic Tests
# ---------------------------------------------------------------------------

class TestGridLevels:
    def test_correct_number_of_levels(self):
        levels = calculate_grid_levels(100, 200, 10)
        assert len(levels) == 11  # num_grids + 1

    def test_levels_bounds(self):
        levels = calculate_grid_levels(50000, 60000, 5)
        assert levels[0] == pytest.approx(50000)
        assert levels[-1] == pytest.approx(60000)

    def test_levels_evenly_spaced(self):
        levels = calculate_grid_levels(100, 200, 4)
        spacings = [levels[i+1] - levels[i] for i in range(len(levels)-1)]
        assert all(abs(s - spacings[0]) < 1e-6 for s in spacings)

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError):
            calculate_grid_levels(200, 100, 10)  # lower > upper

    def test_too_few_grids_raises(self):
        with pytest.raises(ValueError):
            calculate_grid_levels(100, 200, 1)


class TestGridSpacing:
    def test_spacing_calculation(self):
        spacing = get_grid_spacing(10000, 20000, 10)
        assert spacing == pytest.approx(1000.0)

    def test_spacing_non_integer(self):
        spacing = get_grid_spacing(100, 103, 3)
        assert spacing == pytest.approx(1.0)


class TestAmountPerGrid:
    def test_basic_amount(self):
        # 100 USDT / 10 grids / 1000 price / 1 leverage = 0.01 coins
        amount = calculate_amount_per_grid(100, 10, 1000, leverage=1)
        assert amount == pytest.approx(0.01)

    def test_leverage_scales_amount(self):
        amount_no_lev = calculate_amount_per_grid(100, 10, 1000, leverage=1)
        amount_with_lev = calculate_amount_per_grid(100, 10, 1000, leverage=5)
        assert amount_with_lev == pytest.approx(amount_no_lev * 5)


class TestSplitLevels:
    def setup_method(self):
        self.levels = [100, 110, 120, 130, 140]  # 5 Levels, 4 Gaps

    def test_neutral_mode(self):
        buy_levels, sell_levels = split_levels_by_price(self.levels, current_price=125, mode='neutral')
        assert all(p < 125 for p in buy_levels)
        assert all(p > 125 for p in sell_levels)

    def test_long_mode_no_sell_levels(self):
        buy_levels, sell_levels = split_levels_by_price(self.levels, current_price=125, mode='long')
        assert len(sell_levels) == 0
        assert len(buy_levels) > 0

    def test_short_mode_no_buy_levels(self):
        buy_levels, sell_levels = split_levels_by_price(self.levels, current_price=125, mode='short')
        assert len(buy_levels) == 0
        assert len(sell_levels) > 0

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            split_levels_by_price(self.levels, 125, mode='invalid')


class TestNextLevels:
    def setup_method(self):
        self.levels = [100.0, 110.0, 120.0, 130.0, 140.0]

    def test_next_sell_after_buy(self):
        next_sell = find_next_sell_level(110.0, self.levels)
        assert next_sell == pytest.approx(120.0)

    def test_next_buy_after_sell(self):
        next_buy = find_next_buy_level(120.0, self.levels)
        assert next_buy == pytest.approx(110.0)

    def test_no_sell_at_top(self):
        result = find_next_sell_level(140.0, self.levels)
        assert result is None

    def test_no_buy_at_bottom(self):
        result = find_next_buy_level(100.0, self.levels)
        assert result is None


class TestProfitPerCycle:
    def test_positive_profit(self):
        # Spacing 1000, amount 0.01 → gross = 10 USDT
        pnl = profit_per_cycle(1000, 0.01, fee_pct=0.0)
        assert pnl == pytest.approx(10.0)

    def test_fee_reduces_profit(self):
        pnl_no_fee = profit_per_cycle(1000, 0.01, fee_pct=0.0)
        pnl_with_fee = profit_per_cycle(1000, 0.01, fee_pct=0.1)
        assert pnl_with_fee < pnl_no_fee


class TestPriceInRange:
    def test_in_range(self):
        assert price_in_range(105, 100, 110) is True

    def test_at_boundary(self):
        assert price_in_range(100, 100, 110) is True
        assert price_in_range(110, 100, 110) is True

    def test_out_of_range(self):
        assert price_in_range(99, 100, 110) is False
        assert price_in_range(111, 100, 110) is False


# ---------------------------------------------------------------------------
# Config-Datei Tests
# ---------------------------------------------------------------------------

class TestConfig:
    def test_example_config_exists(self):
        configs_dir = os.path.join(PROJECT_ROOT, 'src', 'gbot', 'strategy', 'configs')
        configs = [f for f in os.listdir(configs_dir) if f.endswith('.json')] if os.path.isdir(configs_dir) else []
        assert len(configs) >= 1, "Mindestens eine Config-Datei muss vorhanden sein"

    def test_example_config_valid(self):
        config_path = os.path.join(PROJECT_ROOT, 'src', 'gbot', 'strategy', 'configs', 'config_BTC_USDT_USDT.json')
        if not os.path.exists(config_path):
            pytest.skip("Beispiel-Config nicht vorhanden")

        with open(config_path) as f:
            config = json.load(f)

        assert 'market' in config
        assert 'grid' in config
        assert 'risk' in config
        assert 'symbol' in config['market']
        assert 'lower_price' in config['grid']
        assert 'upper_price' in config['grid']
        assert config['grid']['lower_price'] < config['grid']['upper_price']
        assert config['risk']['total_investment_usdt'] > 0


class TestProjectStructure:
    def test_required_files_exist(self):
        required = [
            'master_runner.py',
            'settings.json',
            'requirements.txt',
            'install.sh',
            'update.sh',
            'src/gbot/__init__.py',
            'src/gbot/strategy/run.py',
            'src/gbot/strategy/grid_logic.py',
            'src/gbot/utils/exchange.py',
            'src/gbot/utils/trade_manager.py',
            'src/gbot/utils/telegram.py',
            'src/gbot/utils/guardian.py',
        ]
        for rel_path in required:
            full_path = os.path.join(PROJECT_ROOT, rel_path)
            assert os.path.exists(full_path), f"Datei fehlt: {rel_path}"
