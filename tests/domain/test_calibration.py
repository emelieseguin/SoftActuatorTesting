from __future__ import annotations

import math

import pytest

from soft_actuator_testing.domain.calibration import (
    CalibrationError,
    CalibrationModel,
    CalibrationModelType,
    CalibrationSample,
    FitQualityPolicy,
    VoltageDomain,
    fit_calibration,
)


def test_linear_fit_uses_legacy_pressure_from_voltage_coefficient_order() -> None:
    fit = fit_calibration(
        [
            CalibrationSample(0.0, 0.1),
            CalibrationSample(100.0, 1.1),
            CalibrationSample(200.0, 2.1),
        ],
        CalibrationModelType.LINEAR,
    )

    assert fit.model.coefficients == pytest.approx((100.0, -10.0))
    assert fit.model.apply(1.1) == pytest.approx(100.0)
    assert fit.adequacy.is_adequate is True
    assert fit.adequacy.r_squared == pytest.approx(1.0)


def test_quadratic_fit_requires_three_distinct_samples_and_applies() -> None:
    samples = [
        CalibrationSample(1.0, 0.0),
        CalibrationSample(6.0, 1.0),
        CalibrationSample(17.0, 2.0),
    ]
    fit = fit_calibration(samples, CalibrationModelType.QUADRATIC)

    assert fit.model.coefficients == pytest.approx((3.0, 2.0, 1.0))
    assert fit.model.apply(2.0) == pytest.approx(17.0)
    with pytest.raises(CalibrationError, match="at least 3 samples"):
        fit_calibration(samples[:2], CalibrationModelType.QUADRATIC)
    with pytest.raises(CalibrationError, match="distinct voltages"):
        fit_calibration(
            [CalibrationSample(1.0, 1.0), CalibrationSample(2.0, 1.0), CalibrationSample(3.0, 1.0)],
            CalibrationModelType.QUADRATIC,
        )


@pytest.mark.parametrize("coefficients", [(1.0,), (1.0, 2.0, 3.0)])
def test_model_rejects_wrong_linear_coefficient_arity(coefficients: tuple[float, ...]) -> None:
    with pytest.raises(CalibrationError, match="exactly 2 coefficients"):
        CalibrationModel(CalibrationModelType.LINEAR, coefficients)


@pytest.mark.parametrize("coefficients", [(1.0, 2.0), (1.0, 2.0, 3.0, 4.0)])
def test_model_rejects_wrong_quadratic_coefficient_arity(coefficients: tuple[float, ...]) -> None:
    with pytest.raises(CalibrationError, match="exactly 3 coefficients"):
        CalibrationModel(CalibrationModelType.QUADRATIC, coefficients)


def test_model_rejects_nonfinite_values_and_out_of_domain_application() -> None:
    with pytest.raises(CalibrationError, match="finite"):
        CalibrationSample(math.nan, 1.0)
    with pytest.raises(CalibrationError, match="finite"):
        CalibrationModel(CalibrationModelType.QUADRATIC, (1.0, math.inf, 2.0))

    model = CalibrationModel(
        CalibrationModelType.LINEAR,
        (100.0, -10.0),
        VoltageDomain(0.1, 2.1),
    )
    with pytest.raises(CalibrationError, match="outside"):
        model.apply(2.2)
    assert model.apply(2.2, require_in_domain=False) == pytest.approx(210.0)


def test_rank_deficient_and_poorly_conditioned_fits_are_rejected() -> None:
    with pytest.raises(CalibrationError, match="distinct voltages"):
        fit_calibration(
            [CalibrationSample(0, 1), CalibrationSample(1, 1)],
            CalibrationModelType.LINEAR,
        )
    with pytest.raises(CalibrationError, match="poorly conditioned"):
        fit_calibration(
            [CalibrationSample(0, 1.0), CalibrationSample(1, 1.0 + 1e-12)],
            CalibrationModelType.LINEAR,
        )


def test_quadratic_adequacy_reports_metrics_and_residuals() -> None:
    fit = fit_calibration(
        [
            CalibrationSample(0, 0),
            CalibrationSample(1, 1),
            CalibrationSample(1, 2),
            CalibrationSample(1, 3),
        ],
        CalibrationModelType.QUADRATIC,
        quality_policy=FitQualityPolicy(maximum_rmse_kpa=0.01),
    )
    assert fit.adequacy.is_adequate is False
    assert fit.adequacy.reason is not None
    assert fit.adequacy.max_abs_residual_kpa > 0
    assert len(fit.residuals) == 4
