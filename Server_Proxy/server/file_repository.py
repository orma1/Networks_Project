"""
FILE REPOSITORY
═════════════════════════════════════════════════════════════

Abstracts file access for video streaming.

Responsibilities:
- Locate video files (with quality variants)
- Provide file metadata (size, existence)
- Handle quality variant mapping (video.mp4 → video_720.mp4)
- Validate file access

Does NOT handle:
- Actual file reading/streaming (that's RUDPSession/TCPHandler's job)
- Network transfer (that's the protocol layer)
- Session management (that's SessionManager's job)

Quality Mapping:
- "auto" → base filename (e.g., video.mp4)
- "480", "720", "1080" → quality variants (e.g., video_720.mp4)
"""

import os
from typing import Optional, List
from pathlib import Path
from Server_Proxy.shared.streaming_interfaces import FileRepository


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL FILE REPOSITORY
# ══════════════════════════════════════════════════════════════════════════════

class LocalFileRepository:
    """
    File repository for local filesystem.
    
    Implements FileRepository interface for accessing video files
    stored on the local disk with quality variants.
    
    File naming convention:
    - Base: video.mp4
    - 480p: video_480.mp4
    - 720p: video_720.mp4
    - 1080p: video_1080.mp4
    
    Example:
        >>> repo = LocalFileRepository(base_dir="/videos")
        >>> path = repo.get_file_path("movie.mp4", quality="720")
        >>> print(path)
        /videos/movie_720.mp4
    """
    
    def __init__(self, base_dir: str = "./videos"):
        """
        Initialize file repository.
        
        Args:
            base_dir: Base directory containing video files
            
        Raises:
            ValueError: If base_dir doesn't exist
            
        Example:
            >>> repo = LocalFileRepository(base_dir="./videos")
        """
        self.base_dir = Path(base_dir)
        
        if not self.base_dir.exists():
            raise ValueError(f"Base directory does not exist: {base_dir}")
        
        if not self.base_dir.is_dir():
            raise ValueError(f"Base directory is not a directory: {base_dir}")
    
    # ── FileRepository Interface Implementation ──────────────────────────
    
    def get_file_path(self, filename: str, quality: str = "auto") -> str:
        """
        Resolve filename to full path with quality variant.
        
        Implements: FileRepository.get_file_path()
        
        Args:
            filename: Base filename (e.g., "video.mp4")
            quality: Quality level ("auto", "480", "720", "1080")
            
        Returns:
            Full filesystem path
            
        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If filename contains path traversal
            
        Example:
            >>> path = repo.get_file_path("movie.mp4", quality="720")
            >>> print(path)
            /videos/movie_720.mp4
        """
        # Sanitize filename (prevent path traversal)
        safe_filename = os.path.basename(filename)
        
        if safe_filename != filename:
            raise ValueError(
                f"Filename contains path separators: {filename}"
            )
        
        # Map quality to filename variant
        if quality == "auto":
            target_filename = safe_filename
        else:
            # Insert quality before extension
            # video.mp4 → video_720.mp4
            name, ext = os.path.splitext(safe_filename)
            target_filename = f"{name}_{quality}{ext}"
        
        # Resolve full path
        full_path = self.base_dir / target_filename
        
        # Check existence
        if not full_path.exists():
            raise FileNotFoundError(
                f"File not found: {target_filename} "
                f"(quality={quality}, base={filename})"
            )
        
        return str(full_path)
    
    def get_file_size(self, filename: str, quality: str = "auto") -> int:
        """
        Get file size without opening.
        
        Implements: FileRepository.get_file_size()
        
        Args:
            filename: Base filename
            quality: Quality level
            
        Returns:
            File size in bytes
            
        Raises:
            FileNotFoundError: If file doesn't exist
            OSError: On filesystem errors
            
        Example:
            >>> size = repo.get_file_size("movie.mp4", quality="720")
            >>> print(f"Size: {size:,} bytes")
            Size: 52,428,800 bytes
        """
        path = self.get_file_path(filename, quality)
        return os.path.getsize(path)
    
    def file_exists(self, filename: str, quality: str = "auto") -> bool:
        """
        Check if file exists.
        
        Implements: FileRepository.file_exists()
        
        Args:
            filename: Base filename
            quality: Quality level
            
        Returns:
            True if file exists and is accessible
            
        Example:
            >>> if repo.file_exists("movie.mp4", quality="720"):
            ...     print("File available")
            File available
        """
        try:
            path = self.get_file_path(filename, quality)
            return os.path.isfile(path)
        except (FileNotFoundError, ValueError):
            return False
    
    # ── Additional Helper Methods ────────────────────────────────────────
    
    def get_available_qualities(self, filename: str) -> List[str]:
        """
        Get list of available quality variants for a file.
        
        Args:
            filename: Base filename
            
        Returns:
            List of available qualities (e.g., ["480", "720", "1080"])
            
        Example:
            >>> qualities = repo.get_available_qualities("movie.mp4")
            >>> print(qualities)
            ['480', '720', '1080']
        """
        safe_filename = os.path.basename(filename)
        name, ext = os.path.splitext(safe_filename)
        
        available = []
        
        # Check each standard quality
        for quality in ["480", "720", "1080"]:
            variant = f"{name}_{quality}{ext}"
            if (self.base_dir / variant).exists():
                available.append(quality)
        
        # Also check base file (auto)
        if (self.base_dir / safe_filename).exists():
            available.insert(0, "auto")
        
        return available
    
    def list_files(self, extension: str = ".mp4") -> List[str]:
        """
        List all files in repository with given extension.
        
        Args:
            extension: File extension to filter (default: ".mp4")
            
        Returns:
            List of filenames (base names only, not full paths)
            
        Example:
            >>> files = repo.list_files(extension=".mp4")
            >>> for f in files:
            ...     print(f)
            movie.mp4
            video.mp4
        """
        files = []
        
        for path in self.base_dir.iterdir():
            if path.is_file() and path.suffix == extension:
                # Only include base files (not quality variants)
                if "_480" not in path.stem and \
                   "_720" not in path.stem and \
                   "_1080" not in path.stem:
                    files.append(path.name)
        
        return sorted(files)
    
    def get_total_size(self, filename: str) -> int:
        """
        Get total size of all quality variants.
        
        Args:
            filename: Base filename
            
        Returns:
            Total size in bytes across all variants
            
        Example:
            >>> total = repo.get_total_size("movie.mp4")
            >>> print(f"Total: {total / 1024**2:.1f} MB")
            Total: 250.5 MB
        """
        total = 0
        qualities = self.get_available_qualities(filename)
        
        for quality in qualities:
            try:
                total += self.get_file_size(filename, quality)
            except (FileNotFoundError, OSError):
                continue
        
        return total
    
    def validate_repository(self) -> dict:
        """
        Validate repository structure and return report.
        
        Returns:
            Dictionary with validation results
            
        Example:
            >>> report = repo.validate_repository()
            >>> print(f"Files: {report['total_files']}")
            >>> print(f"Issues: {report['issues']}")
        """
        report = {
            "base_dir": str(self.base_dir),
            "exists": self.base_dir.exists(),
            "is_readable": os.access(self.base_dir, os.R_OK),
            "total_files": 0,
            "base_files": [],
            "quality_variants": {},
            "missing_variants": [],
            "issues": [],
        }
        
        if not report["exists"]:
            report["issues"].append("Base directory does not exist")
            return report
        
        if not report["is_readable"]:
            report["issues"].append("Base directory is not readable")
            return report
        
        # Scan files
        base_files = self.list_files()
        report["total_files"] = len(base_files)
        report["base_files"] = base_files
        
        # Check quality variants for each base file
        for filename in base_files:
            qualities = self.get_available_qualities(filename)
            report["quality_variants"][filename] = qualities
            
            # Warn if missing common qualities
            for expected_q in ["480", "720", "1080"]:
                if expected_q not in qualities:
                    report["missing_variants"].append(
                        f"{filename} missing {expected_q}p variant"
                    )
        
        return report
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"LocalFileRepository(base_dir={self.base_dir})"


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import tempfile
    import shutil
    
    print("Running LocalFileRepository self-tests...\n")
    
    # Create temporary directory with test files
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Create test files
        test_files = [
            "video.mp4",
            "video_480.mp4",
            "video_720.mp4",
            "video_1080.mp4",
            "movie.mp4",
            "movie_720.mp4",
        ]
        
        for filename in test_files:
            path = os.path.join(temp_dir, filename)
            with open(path, "w") as f:
                f.write(f"test content for {filename}\n" * 100)
        
        # Test 1: Initialization
        repo = LocalFileRepository(base_dir=temp_dir)
        assert str(repo.base_dir) == temp_dir
        print("✓ Initialization")
        
        # Test 2: Get file path (base)
        path = repo.get_file_path("video.mp4", quality="auto")
        assert path == os.path.join(temp_dir, "video.mp4")
        print("✓ Get file path (auto)")
        
        # Test 3: Get file path (quality variant)
        path = repo.get_file_path("video.mp4", quality="720")
        assert path == os.path.join(temp_dir, "video_720.mp4")
        print("✓ Get file path (quality variant)")
        
        # Test 4: File exists
        assert repo.file_exists("video.mp4", quality="auto") == True
        assert repo.file_exists("video.mp4", quality="720") == True
        assert repo.file_exists("video.mp4", quality="360") == False
        assert repo.file_exists("nonexistent.mp4") == False
        print("✓ File exists check")
        
        # Test 5: Get file size
        size = repo.get_file_size("video.mp4", quality="720")
        assert size > 0
        print("✓ Get file size")
        
        # Test 6: Get available qualities
        qualities = repo.get_available_qualities("video.mp4")
        assert "auto" in qualities
        assert "480" in qualities
        assert "720" in qualities
        assert "1080" in qualities
        print("✓ Get available qualities")
        
        # Test 7: List files
        files = repo.list_files(extension=".mp4")
        assert "video.mp4" in files
        assert "movie.mp4" in files
        assert "video_720.mp4" not in files  # Should exclude variants
        print("✓ List files")
        
        # Test 8: Path traversal protection
        try:
            repo.get_file_path("../../../etc/passwd")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "path separators" in str(e)
        print("✓ Path traversal protection")
        
        # Test 9: FileNotFoundError
        try:
            repo.get_file_path("nonexistent.mp4")
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            pass
        print("✓ FileNotFoundError on missing file")
        
        # Test 10: Validate repository
        report = repo.validate_repository()
        assert report["exists"] == True
        assert report["is_readable"] == True
        assert report["total_files"] == 2  # video.mp4 and movie.mp4
        print("✓ Validate repository")
        
        # Test 11: Get total size
        total = repo.get_total_size("video.mp4")
        assert total > 0
        print("✓ Get total size")
        
        print("\n✅ All LocalFileRepository tests passed!")
        print("\nExample usage:")
        print("  repo = LocalFileRepository(base_dir='./videos')")
        print("  path = repo.get_file_path('movie.mp4', quality='720')")
        print("  size = repo.get_file_size('movie.mp4', quality='720')")
        print("  if repo.file_exists('movie.mp4', quality='1080'):")
        print("      # File available")
    
    finally:
        # Cleanup
        shutil.rmtree(temp_dir)
