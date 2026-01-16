from pymongo import MongoClient
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

mongo_uri = os.getenv("MONGODB_URI")
client = MongoClient(mongo_uri)
db = client["mentora"]
users_col = db["appUsers"]

class ContributionService:
    """Centralized service for managing contribution points"""
    
    @staticmethod
    def add_points(user_email: str, points: int, reason: str = ""):
        """Add contribution points to a user"""
        result = users_col.update_one(
            {"email": user_email},
            {
                "$inc": {"contributionPoints": points},
                "$set": {"lastContributionUpdate": datetime.now()}
            }
        )
        if result.modified_count > 0:
            # Check badge eligibility after points update
            from services.badge_service import BadgeService
            BadgeService.check_and_assign_badges(user_email)
        return result.modified_count > 0
    
    @staticmethod
    def get_user_stats(user_email: str):
        """Get user contribution statistics"""
        user = users_col.find_one({"email": user_email})
        if not user:
            return {
                "contributionPoints": 0,
                "acceptedAnswersCount": 0,
                "uploadedPdfCount": 0,
                "completedQuizCount": 0,
                "askQuestionCount": 0,
                "answerQuestionCount": 0
            }
        return {
            "contributionPoints": user.get("contributionPoints", 0),
            "acceptedAnswersCount": user.get("acceptedAnswersCount", 0),
            "uploadedPdfCount": user.get("uploadedPdfCount", 0),
            "completedQuizCount": user.get("completedQuizCount", 0),
            "askQuestionCount": user.get("askQuestionCount", 0),
            "answerQuestionCount": user.get("answerQuestionCount", 0)
        }
    
    @staticmethod
    def increment_field(user_email: str, field: str, amount: int = 1):
        """Increment a specific field in user document"""
        users_col.update_one(
            {"email": user_email},
            {"$inc": {field: amount}}
        )
