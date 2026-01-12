from datetime import datetime
from bson import ObjectId

def serialize(doc):
    doc["_id"] = str(doc["_id"])

    # Make sure created_at exists
    if "created_at" in doc and doc["created_at"]:
        doc["created_at"] = doc["created_at"].isoformat()
    else:
        doc["created_at"] = datetime.now().isoformat()

    # Serialize answers
    for a in doc.get("answers", []):
        a["_id"] = str(a["_id"])
        if "created_at" in a and a["created_at"]:
            a["created_at"] = a["created_at"].isoformat()
        else:
            a["created_at"] = datetime.now().isoformat()

        # Serialize comments in answer
        for c in a.get("comments", []):
            c["_id"] = str(c["_id"])
            if "created_at" in c and c["created_at"]:
                c["created_at"] = c["created_at"].isoformat()
            else:
                c["created_at"] = datetime.now().isoformat()

    # Serialize question comments
    for c in doc.get("comments", []):
        c["_id"] = str(c["_id"])
        if "created_at" in c and c["created_at"]:
            c["created_at"] = c["created_at"].isoformat()
        else:
            c["created_at"] = datetime.now().isoformat()

    # Accepted answer
    if doc.get("accepted_answer_id"):
        doc["accepted_answer_id"] = str(doc["accepted_answer_id"])

    return doc
