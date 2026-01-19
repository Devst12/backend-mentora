import cloudinary.uploader
from config.cloudinary import cloudinary

async def upload_image(file, folder: str):
    """
    Upload image to Cloudinary
    
    Args:
        file: FastAPI UploadFile object
        folder: Folder path (e.g., 'mentora/editor-images' or 'mentora/badges')
    
    Returns:
        dict with 'secure_url' and 'public_id'
    """
    try:
        # Read file content
        file_content = await file.read()
        await file.seek(0)  # Reset file pointer
        
        result = cloudinary.uploader.upload(
            file_content,
            folder=folder,
            resource_type="image"
        )
        return {
            "secure_url": result.get("secure_url"),
            "public_id": result.get("public_id")
        }
    except Exception as e:
        raise Exception(f"Cloudinary upload failed: {str(e)}")
