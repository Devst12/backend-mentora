from pymongo import MongoClient
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

mongo_uri = os.getenv("MONGODB_URI")
client = MongoClient(mongo_uri)
db = client["mentora"]
users_col = db["appUsers"]
badges_col = db["badges"]

class BadgeService:
    """Service for managing badges and badge assignments"""
    
    @staticmethod
    def initialize_default_badges():
        """Initialize default badges if they don't exist"""
        default_badges = [
            {
                "name": "First Question",
                "description": "Asked your first question",
                "iconUrl": "",
                "ruleType": "question_count",
                "threshold": 1,
                "createdAt": datetime.now()
            },
            {
                "name": "Curious Mind",
                "description": "Asked 10 questions",
                "iconUrl": "",
                "ruleType": "question_count",
                "threshold": 10,
                "createdAt": datetime.now()
            },
            {
                "name": "Helper",
                "description": "Answered 5 questions",
                "iconUrl": "",
                "ruleType": "answer_count",
                "threshold": 5,
                "createdAt": datetime.now()
            },
            {
                "name": "Expert",
                "description": "Got 3 accepted answers",
                "iconUrl": "",
                "ruleType": "accepted_answer_count",
                "threshold": 3,
                "createdAt": datetime.now()
            },
            {
                "name": "Contributor",
                "description": "Earned 100 contribution points",
                "iconUrl": "",
                "ruleType": "points",
                "threshold": 100,
                "createdAt": datetime.now()
            },
            {
                "name": "Scholar",
                "description": "Earned 500 contribution points",
                "iconUrl": "",
                "ruleType": "points",
                "threshold": 500,
                "createdAt": datetime.now()
            },
            {
                "name": "PDF Master",
                "description": "Uploaded 5 PDFs",
                "iconUrl": "",
                "ruleType": "pdf_upload_count",
                "threshold": 5,
                "createdAt": datetime.now()
            },
            {
                "name": "Quiz Champion",
                "description": "Completed 10 quizzes",
                "iconUrl": "",
                "ruleType": "quiz_completion_count",
                "threshold": 10,
                "createdAt": datetime.now()
            }
        ]
        
        for badge in default_badges:
            badges_col.update_one(
                {"name": badge["name"]},
                {"$setOnInsert": badge},
                upsert=True
            )
    
    @staticmethod
    def check_and_assign_badges(user_email: str):
        """Check user eligibility and assign badges"""
        user = users_col.find_one({"email": user_email})
        if not user:
            return
        
        # Initialize default badges if needed
        BadgeService.initialize_default_badges()
        
        user_badges = user.get("badges", [])
        user_badge_ids = [str(b) if isinstance(b, dict) else str(b) for b in user_badges]
        
        # Get user stats
        from services.contribution_service import ContributionService
        stats = ContributionService.get_user_stats(user_email)
        
        # Get all badges
        all_badges = list(badges_col.find({}))
        
        for badge in all_badges:
            badge_id = str(badge["_id"])
            if badge_id in user_badge_ids:
                continue  # Already has this badge
            
            # Check if user meets badge criteria
            if BadgeService._meets_badge_criteria(badge, stats, user):
                # Assign badge
                users_col.update_one(
                    {"email": user_email},
                    {"$addToSet": {"badges": badge_id}}
                )
    
    @staticmethod
    def _meets_badge_criteria(badge, stats, user):
        """Check if user meets badge criteria"""
        rule_type = badge.get("ruleType")
        threshold = badge.get("threshold", 0)
        
        if rule_type == "points":
            return stats.get("contributionPoints", 0) >= threshold
        elif rule_type == "question_count":
            # Count questions from questions collection
            questions_col = db["questions"]
            count = questions_col.count_documents({"authorId": user["email"]})
            return count >= threshold
        elif rule_type == "answer_count":
            # Count answers from answers collection
            answers_col = db["answers"]
            count = answers_col.count_documents({"authorId": user["email"]})
            return count >= threshold
        elif rule_type == "accepted_answer_count":
            return stats.get("acceptedAnswersCount", 0) >= threshold
        elif rule_type == "pdf_upload_count":
            return stats.get("uploadedPdfCount", 0) >= threshold
        elif rule_type == "quiz_completion_count":
            return stats.get("completedQuizCount", 0) >= threshold
        
        return False
    
    @staticmethod
    def get_user_badges(user_email: str):
        """Get all badges for a user (earned and locked)"""
        user = users_col.find_one({"email": user_email})
        if not user:
            return {"earned": [], "locked": []}
        
        user_badge_ids = [str(b) if isinstance(b, dict) else str(b) for b in user.get("badges", [])]
        all_badges = list(badges_col.find({}))
        
        earned = []
        locked = []
        
        for badge in all_badges:
            badge_id = str(badge["_id"])
            badge_data = {
                "id": badge_id,
                "name": badge.get("name"),
                "description": badge.get("description"),
                "iconUrl": badge.get("iconUrl"),
                "ruleType": badge.get("ruleType"),
                "threshold": badge.get("threshold")
            }
            
            if badge_id in user_badge_ids:
                earned.append(badge_data)
            else:
                locked.append(badge_data)
        
        return {"earned": earned, "locked": locked}
    
    @staticmethod
    def get_all_badges():
        """Get all available badges"""
        badges = list(badges_col.find({}))
        return [{
            "id": str(b["_id"]),
            "name": b.get("name"),
            "description": b.get("description"),
            "iconUrl": b.get("iconUrl"),
            "ruleType": b.get("ruleType"),
            "threshold": b.get("threshold"),
            "createdAt": b.get("createdAt").isoformat() if b.get("createdAt") else None
        } for b in badges]
