"""Dashboard + Reports URL routes."""
from django.urls import path

from .views import DashboardSummaryView
from .views_missing_reports import (
    ExpiringLeasesReportView,
    RentBalancesReportView,
    RentOverpaymentsReportView,
    TenantStatementReportView,
    UnitStatementReportView,
    VacantUnitsReportView,
)
from .views_reports import (
    AccountingDashboardView,
    AgingArrearsReportView,
    AnnualIncomeSummaryView,
    ArrearsReportView,
    ExpenseBreakdownReportView,
    MonthlyCollectionReportView,
    MoveInOutLogView,
    OccupancyHistoryView,
    ProfitLossReportView,
    TenantPaymentHistoryView,
    TrialBalanceView,
)

app_name = "dashboard"

urlpatterns = [
    path("dashboard/summary/", DashboardSummaryView.as_view(), name="summary"),
    path("reports/monthly-collection/", MonthlyCollectionReportView.as_view(), name="monthly-collection"),
    path("reports/annual-income/", AnnualIncomeSummaryView.as_view(), name="annual-income"),
    path("reports/arrears/", ArrearsReportView.as_view(), name="arrears-report"),
    path("reports/aging-arrears/", AgingArrearsReportView.as_view(), name="aging-arrears-report"),
    path("reports/tenant-history/<int:tenant_id>/", TenantPaymentHistoryView.as_view(), name="tenant-history"),
    path("reports/occupancy/", OccupancyHistoryView.as_view(), name="occupancy"),
    path("reports/move-log/", MoveInOutLogView.as_view(), name="move-log"),
    path("reports/profit-loss/", ProfitLossReportView.as_view(), name="profit-loss"),
    path("reports/trial-balance/", TrialBalanceView.as_view(), name="trial-balance"),
    path("reports/expense-breakdown/", ExpenseBreakdownReportView.as_view(), name="expense-breakdown"),
    path("reports/accounting/", AccountingDashboardView.as_view(), name="accounting"),
    # Routes the Reports page has always called and never had — see
    # views_missing_reports for what each one is and why "landlord-statement"
    # was removed from the UI instead of being invented here.
    path("reports/rent-balances/", RentBalancesReportView.as_view(), name="rent-balances"),
    path("reports/rent-overpayments/", RentOverpaymentsReportView.as_view(), name="rent-overpayments"),
    path("reports/expiring-leases/", ExpiringLeasesReportView.as_view(), name="expiring-leases"),
    path("reports/vacant-units/", VacantUnitsReportView.as_view(), name="vacant-units"),
    path("reports/tenant-statement/<int:tenant_id>/", TenantStatementReportView.as_view(), name="tenant-statement"),
    path("reports/unit-statement/<int:unit_id>/", UnitStatementReportView.as_view(), name="unit-statement"),
]
