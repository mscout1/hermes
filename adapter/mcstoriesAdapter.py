import re
import urllib.parse
from typing import Optional

from bs4 import BeautifulSoup

import scrape
import util
from adapter.adapter import Adapter, edumpContent
from htypes import FicType, FicId
from schema import OilTimestamp
from store import Fic, Language
from store import FicStatus

story_index_path_re = re.compile(r"/(?P<lid>\w+)/index\.html")
story_chapter_path_re = re.compile(r"/(?P<lid>\w+)/(?P<clid>\w+)\.html")
relative_chapter_path_re = re.compile(r"(?P<clid>\w+)\.html")


def strip_nonnumeric(s):
    return ''.join(c for c in s if c.isdigit())


class McStoriesAdapter(Adapter):
    def __init__(self) -> None:
        super().__init__(
            True,
            "https://mcstories.com/",
            "mcstories.com",
            FicType.mcstories,
            "mcstories",
        )

    def tryParseUrl(self, url: str) -> Optional[FicId]:
        path = urllib.parse.urlparse(url).path

        if m := story_index_path_re.fullmatch(path):
            lid = m.group("lid")
        elif m := story_chapter_path_re.fullmatch(path):
            lid = m.group("clid")
        else:
            return None
        return FicId(self.ftype, lid)
    
    def constructUrl(self, fic, chapter=None):
        if chapter is None:
            path = f"/{fic.localId}/index.html"
        else:
            path = f"/{fic.localId}/{chapter.localId}.html"
        return urllib.parse.urljoin(self.baseUrl, path)

    def create(self, fic: Fic) -> Fic:

        fic.url = self.constructUrl(fic)

        data = scrape.scrape(fic.url)
        edumpContent(data["raw"], "mcstories")
        fic = self.parseInfoInto(fic, data["raw"])
        fic.upsert()
        
        return fic

    def absolute_author_link(self, rel_author_link):
        if rel_author_link.startswith('..'):
            path = rel_author_link[len('..'):]
        else:
            path = rel_author_link
        return urllib.parse.urljoin(self.baseUrl, path)

    def absolute_chapter_link(self, fic, rel_chapter_link):
        path = f"{fic.localId}/{rel_chapter_link}"
        return urllib.parse.urljoin(self.baseUrl, path)

    def get_chapter_meta(self, table, fic):
        chapters = []
        for chapter_row_dict in util.dicts_from_table(table):
            name_cell = chapter_row_dict['Chapter']
            words_cell = chapter_row_dict['Length']
            date_cell = chapter_row_dict['Added']

            updated_cell = chapter_row_dict.get('Updated', date_cell)
            relative_chapter_path = name_cell.find('a')['href']
            clid = relative_chapter_path_re.fullmatch(relative_chapter_path).group('clid')
            published = util.parseDateAsUnix(date_cell.string, fic.fetched)

            if updated_cell.string and updated_cell.string.strip():
                updated = util.parseDateAsUnix(updated_cell.string, fic.fetched)
            else:
                updated = published
            chapters.append({
                'clid': clid,
                'title': name_cell.string,
                'chapter_link': self.absolute_chapter_link(fic, relative_chapter_path),
                'words': int(strip_nonnumeric(words_cell.string)),
                'published': published,
                'updated': updated,
            })
        return chapters

    def parseInfoInto(self, fic: Fic, html: str):
        soup = BeautifulSoup(html, "html.parser")

        fic.fetched = OilTimestamp.now()
        fic.languageId = Language.getId("English")  # All stories on mcstories are presumed english
        fic.title = soup.find(class_="title").string.strip()
        fic.description = soup.find(class_="synopsis").get_text().strip()
        fic.ageRating = "M"  # *EROTIC* Mind Control Story Archive

        date_strings = soup.find_all('h3', class_='dateline')
        published_date_string = date_strings.pop(0).string[len("Added "):]  # "Added 18 October 2014"
        if date_strings:
            updated_date_string = date_strings.pop(0).string[len("Updated "):]  # "Updated 18 October 2014"
        else:
            updated_date_string = published_date_string
        publishedUts = util.parseDateAsUnix(published_date_string, fic.fetched)
        updatedUts = util.parseDateAsUnix(updated_date_string, fic.fetched)
        fic.published = OilTimestamp(publishedUts)

        chapter_table = soup.find('table', class_='index')
        if chapter_table:
            chapters = self.get_chapter_meta(chapter_table, fic)
        else:
            chapter_div = soup.find('div', class_='chapter')
            link = chapter_div.find('a')
            relative_chapter_path = link['href']
            clid = relative_chapter_path_re.fullmatch(relative_chapter_path).group('clid')

            chapters = [
             {
                'clid': clid,
                'title': link.string,
                'chapter_link': self.absolute_chapter_link(fic, relative_chapter_path),
                'words': int(strip_nonnumeric(link.next_sibling)),
                'published': publishedUts,
                'updated': publishedUts
            }]

        fic.chapterCount = len(chapters)
        fic.wordCount = sum(chapter['words'] for chapter in chapters)

        fic.reviewCount = 0
        fic.favoriteCount = 0
        fic.followCount = 0

        # The update date of the last chapter is the best estimate of this story's last update
        all_updates = [updatedUts] + [chapter['updated'] for chapter in chapters]
        fic.updated = OilTimestamp(max(all_updates))

        fic.ficStatus = FicStatus.ongoing  # TODO: No indication on this site.

        byline = soup.find("h3", class_="byline")
        authorLink = self.absolute_author_link(byline.find("a")['href'])

        authorUrl = authorLink
        author = byline.find("a").string
        authorId = author  # map pseudo to real?
        self.setAuthor(fic, author, authorUrl, authorId)
        fic.upsert()

        for cid, chapter_meta in enumerate(chapters, 1):
            chap = fic.chapter(cid)
            chap.url = chapter_meta['chapter_link']
            chap.localChapterId = chapter_meta['clid']
            chap.title = chapter_meta['title']
            chap.upsert()

        return fic

    # extract the html text which contains the story itself
    def extractContent(self, fic: Fic, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find('article')
        if article is None:
            edumpContent(html, "mcstories_ec")
            raise Exception("unable to find chapters, e-dumped")

        return str(article)

    def getCurrentInfo(self, fic: Fic) -> Fic:
        fic.url = self.constructUrl(fic)
        data = scrape.scrape(fic.url)
        return self.parseInfoInto(fic, data["raw"])
