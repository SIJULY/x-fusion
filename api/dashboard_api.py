# api/dashboard_api.py
from fastapi import APIRouter
from services.jobs import calculate_dashboard_data

dashboard_router = APIRouter()

@dashboard_router.get('/api/dashboard/live_data')
def get_dashboard_live_data():
    """
    API endpoint for frontend to poll real-time dashboard statistics.
    """
    data = calculate_dashboard_data()
    return data if data else {"error": "Calculation failed"}