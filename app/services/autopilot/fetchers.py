"""Source fetchers for autopilot monitoring."""
import httpx
import xml.etree.ElementTree as ET
import re
from typing import Optional


async def fetch_youtube_channel_videos(
    channel_url: str,
    last_content_id: Optional[str] = None,
) -> list[dict]:
    """
    Fetch recent videos from a YouTube channel.

    Uses YouTube's RSS feed (no API key needed).

    Args:
        channel_url: YouTube channel URL
        last_content_id: ID of last processed video (to stop at)

    Returns:
        List of video dicts with content_id, title, url, published, source_type
    """
    # Extract channel ID from URL
    channel_id = await extract_youtube_channel_id(channel_url)

    # Use YouTube RSS feed
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

    async with httpx.AsyncClient() as client:
        response = await client.get(feed_url, timeout=30.0)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch YouTube feed: {response.status_code}")

    # Parse RSS
    root = ET.fromstring(response.text)
    namespace = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }

    videos = []
    entries = root.findall("atom:entry", namespace)

    for entry in entries[:10]:  # Limit to 10 most recent
        video_id_elem = entry.find("yt:videoId", namespace)
        if video_id_elem is None:
            continue

        video_id = video_id_elem.text

        # Stop if we've seen this video before
        if video_id == last_content_id:
            break

        title_elem = entry.find("atom:title", namespace)
        published_elem = entry.find("atom:published", namespace)

        videos.append({
            "content_id": video_id,
            "title": title_elem.text if title_elem is not None else "Untitled",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "published": published_elem.text if published_elem is not None else None,
            "source_type": "youtube",
        })

    return videos


async def extract_youtube_channel_id(url: str) -> str:
    """
    Extract channel ID from various YouTube URL formats.

    Handles:
    - youtube.com/channel/UC...
    - youtube.com/c/ChannelName
    - youtube.com/@handle
    - youtube.com/user/username
    """
    # Direct channel ID pattern
    channel_match = re.search(r"youtube\.com/channel/([a-zA-Z0-9_-]+)", url)
    if channel_match:
        return channel_match.group(1)

    # For @handle, /c/, and /user/ URLs, we need to fetch the page to get the channel ID
    handle_patterns = [
        r"youtube\.com/@([a-zA-Z0-9_-]+)",
        r"youtube\.com/c/([a-zA-Z0-9_-]+)",
        r"youtube\.com/user/([a-zA-Z0-9_-]+)",
    ]

    for pattern in handle_patterns:
        match = re.search(pattern, url)
        if match:
            # Fetch the page to extract the actual channel ID
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(url, timeout=30.0, follow_redirects=True)
                    if response.status_code == 200:
                        # Look for channel ID in the page content
                        channel_id_match = re.search(
                            r'"channelId":"(UC[a-zA-Z0-9_-]+)"',
                            response.text,
                        )
                        if channel_id_match:
                            return channel_id_match.group(1)

                        # Alternative pattern
                        channel_id_match = re.search(
                            r'channel_id=([a-zA-Z0-9_-]+)',
                            response.text,
                        )
                        if channel_id_match:
                            return channel_id_match.group(1)
                except Exception:
                    pass

            raise ValueError(
                f"Could not extract channel ID from URL: {url}. "
                "Try using the direct channel URL (youtube.com/channel/UC...)"
            )

    raise ValueError(f"Could not parse YouTube URL format: {url}")


async def fetch_rss_feed(
    feed_url: str,
    last_content_id: Optional[str] = None,
) -> list[dict]:
    """
    Fetch items from an RSS or Atom feed.

    Args:
        feed_url: RSS/Atom feed URL
        last_content_id: ID of last processed item (to stop at)

    Returns:
        List of item dicts with content_id, title, url, published, source_type
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(feed_url, timeout=30.0)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch RSS feed: {response.status_code}")

    root = ET.fromstring(response.text)
    items = []

    # Handle RSS 2.0
    if root.tag == "rss":
        entries = root.findall(".//item")
        for entry in entries[:10]:
            guid = entry.find("guid")
            link = entry.find("link")
            content_id = guid.text if guid is not None else (link.text if link is not None else None)

            if not content_id:
                continue

            if content_id == last_content_id:
                break

            title = entry.find("title")
            pub_date = entry.find("pubDate")
            description = entry.find("description")

            items.append({
                "content_id": content_id,
                "title": title.text if title is not None else "Untitled",
                "url": link.text if link is not None else content_id,
                "published": pub_date.text if pub_date is not None else None,
                "source_type": "rss",
                "description": description.text if description is not None else None,
            })

    # Handle Atom feed
    else:
        namespace = {"atom": "http://www.w3.org/2005/Atom"}

        # Try with namespace
        entries = root.findall("atom:entry", namespace)
        if not entries:
            # Try without namespace (some feeds don't use it correctly)
            entries = root.findall("entry")
            namespace = {}

        for entry in entries[:10]:
            if namespace:
                id_elem = entry.find("atom:id", namespace)
                link_elem = entry.find("atom:link", namespace)
                title_elem = entry.find("atom:title", namespace)
                published_elem = entry.find("atom:published", namespace)
            else:
                id_elem = entry.find("id")
                link_elem = entry.find("link")
                title_elem = entry.find("title")
                published_elem = entry.find("published")

            content_id = id_elem.text if id_elem is not None else None
            if not content_id:
                continue

            if content_id == last_content_id:
                break

            url = link_elem.get("href") if link_elem is not None else content_id

            items.append({
                "content_id": content_id,
                "title": title_elem.text if title_elem is not None else "Untitled",
                "url": url,
                "published": published_elem.text if published_elem is not None else None,
                "source_type": "rss",
            })

    return items


async def fetch_podcast_episodes(
    feed_url: str,
    last_content_id: Optional[str] = None,
) -> list[dict]:
    """
    Fetch episodes from a podcast RSS feed.

    Args:
        feed_url: Podcast RSS feed URL
        last_content_id: ID of last processed episode (to stop at)

    Returns:
        List of episode dicts with content_id, title, url, audio_url, published, source_type
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(feed_url, timeout=30.0)

    if response.status_code != 200:
        raise Exception(f"Failed to fetch podcast feed: {response.status_code}")

    root = ET.fromstring(response.text)
    episodes = []
    entries = root.findall(".//item")

    for entry in entries[:10]:
        # Podcast episodes typically have an enclosure with the audio URL
        enclosure = entry.find("enclosure")

        if enclosure is None:
            continue

        audio_url = enclosure.get("url")
        if not audio_url:
            continue

        guid = entry.find("guid")
        content_id = guid.text if guid is not None else audio_url

        if content_id == last_content_id:
            break

        title = entry.find("title")
        pub_date = entry.find("pubDate")
        description = entry.find("description")

        # Get duration if available (iTunes namespace)
        duration = None
        itunes_duration = entry.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration")
        if itunes_duration is not None:
            duration = itunes_duration.text

        episodes.append({
            "content_id": content_id,
            "title": title.text if title is not None else "Untitled Episode",
            "url": audio_url,
            "audio_url": audio_url,
            "published": pub_date.text if pub_date is not None else None,
            "source_type": "podcast",
            "description": description.text if description is not None else None,
            "duration": duration,
        })

    return episodes


async def fetch_article_content(url: str) -> str:
    """
    Fetch and extract main content from an article URL.

    Uses basic HTML parsing to extract the main text content.

    Args:
        url: Article URL

    Returns:
        Extracted article text content
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ContentMultiplier/1.0)"
            },
        )

    if response.status_code != 200:
        raise Exception(f"Failed to fetch article: {response.status_code}")

    html = response.text

    # Simple content extraction - remove scripts, styles, and HTML tags
    import re

    # Remove script and style elements
    html = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", html, flags=re.IGNORECASE)

    # Remove HTML comments
    html = re.sub(r"<!--[\s\S]*?-->", "", html)

    # Try to find main content area
    main_content = None
    for tag in ["article", "main", 'div[class*="content"]', 'div[class*="post"]']:
        pattern = rf"<{tag}[^>]*>([\s\S]*?)</{tag.split('[')[0]}>"
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            main_content = match.group(1)
            break

    if not main_content:
        main_content = html

    # Remove remaining HTML tags
    text = re.sub(r"<[^>]+>", " ", main_content)

    # Clean up whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Decode HTML entities
    import html as html_module
    text = html_module.unescape(text)

    return text[:10000]  # Limit to 10k characters


async def validate_source_url(source_type: str, source_url: str) -> dict:
    """
    Validate a source URL and return info about it.

    Args:
        source_type: Type of source (youtube_channel, rss, podcast)
        source_url: URL to validate

    Returns:
        Dict with validation result and source info
    """
    try:
        if source_type == "youtube_channel":
            channel_id = await extract_youtube_channel_id(source_url)
            items = await fetch_youtube_channel_videos(source_url)
            return {
                "valid": True,
                "source_id": channel_id,
                "item_count": len(items),
                "latest_title": items[0]["title"] if items else None,
            }

        elif source_type == "rss":
            items = await fetch_rss_feed(source_url)
            return {
                "valid": True,
                "item_count": len(items),
                "latest_title": items[0]["title"] if items else None,
            }

        elif source_type == "podcast":
            episodes = await fetch_podcast_episodes(source_url)
            return {
                "valid": True,
                "item_count": len(episodes),
                "latest_title": episodes[0]["title"] if episodes else None,
            }

        else:
            return {"valid": False, "error": f"Unknown source type: {source_type}"}

    except Exception as e:
        return {"valid": False, "error": str(e)}
