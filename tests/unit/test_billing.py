import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
import billing
import models

def test_late_fee_disabled(db_session):
    # Set owner late fee disabled
    owner = db_session.query(models.Owner).first()
    owner.late_fee_enabled = 0
    db_session.commit()
    
    # Calculate late fee - should be 0.0 even if late
    fee = billing.get_late_fee(db_session, billing_month=1, billing_year=2026)
    assert fee == 0.0

def test_late_fee_enabled_not_late(db_session):
    # Set owner late fee enabled
    owner = db_session.query(models.Owner).first()
    owner.late_fee_enabled = 1
    owner.due_day = 15
    owner.late_fee_per_day = 50.0
    db_session.commit()
    
    # Mock datetime.now() to be BEFORE the due day
    # due date will be 2026-05-15, mock today to 2026-05-10
    with patch('billing.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 5, 10)
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        
        fee = billing.get_late_fee(db_session, billing_month=5, billing_year=2026)
        assert fee == 0.0

def test_late_fee_enabled_is_late(db_session):
    owner = db_session.query(models.Owner).first()
    owner.late_fee_enabled = 1
    owner.due_day = 5
    owner.late_fee_per_day = 50.0
    db_session.commit()
    
    # Mock datetime.now() to be AFTER the due day
    # due date will be 2026-05-05, mock today to 2026-05-12 (7 days late)
    with patch('billing.datetime') as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 5, 12)
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        
        fee = billing.get_late_fee(db_session, billing_month=5, billing_year=2026)
        # 7 days * 50 = 350.0
        assert fee == 350.0

def test_late_fee_missing_parameters(db_session):
    owner = db_session.query(models.Owner).first()
    owner.late_fee_enabled = 1
    db_session.commit()
    
    # No month/year provided - should fallback to 0.0 safely
    fee = billing.get_late_fee(db_session, billing_month=None, billing_year=None)
    assert fee == 0.0
