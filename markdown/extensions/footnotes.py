# Footnotes Extension for Python-Markdown
# =======================================

# Adds footnote handling to Python-Markdown.

# See https://Python-Markdown.github.io/extensions/footnotes
# for documentation.

# Copyright The Python Markdown Project

# License: [BSD](https://opensource.org/licenses/bsd-license.php)

"""
Adds footnote handling to Python-Markdown.

See the [documentation](https://Python-Markdown.github.io/extensions/footnotes)
for details.
"""

from __future__ import annotations

from . import Extension
from ..blockprocessors import BlockProcessor
from ..inlinepatterns import InlineProcessor
from ..treeprocessors import Treeprocessor
from ..postprocessors import Postprocessor
from .. import util
from collections import OrderedDict
import re
import copy
import xml.etree.ElementTree as etree

FN_BACKLINK_TEXT = util.STX + "zz1337820767766393qq" + util.ETX
NBSP_PLACEHOLDER = util.STX + "qq3936677670287331zz" + util.ETX
RE_REF_ID = re.compile(r'(fnref)(\d+)')
RE_REFERENCE = re.compile(r'(?<!!)\[\^([^\]]*)\](?!\s*:)')


class FootnoteExtension(Extension):
    """ Footnote Extension. """

    def __init__(self, **kwargs):
        """ Setup configs. """

        self.config = {
            'PLACE_MARKER': [
                '///Footnotes Go Here///', 'The text string that marks where the footnotes go'
            ],
            'UNIQUE_IDS': [
                False, 'Avoid name collisions across multiple calls to `reset()`.'
            ],
            'BACKLINK_TEXT': [
                '&#8617;', "The text string that links from the footnote to the reader's place."
            ],
            'SUPERSCRIPT_TEXT': [
                '{}', "The text string that links from the reader's place to the footnote."
            ],
            'BACKLINK_TITLE': [
                'Jump back to footnote %d in the text',
                'The text string used for the title HTML attribute of the backlink. '
                '%d will be replaced by the footnote number.'
            ],
            'SEPARATOR': [
                ':', 'Footnote separator.'
            ],
            'USE_DEFINITION_ORDER': [
                False, 'Whether to order footnotes by footnote content rather than by footnote label.'
            ]
        }
        """ Default configuration options. """
        super().__init__(**kwargs)

        # In multiple invocations, emit links that don't get tangled.
        self.unique_prefix = 0
        self.found_refs: dict[str, int] = {}
        self.used_refs: set[str] = set()

        # Backward compatibility with old '%d' placeholder
        self.setConfig('BACKLINK_TITLE', self.getConfig("BACKLINK_TITLE").replace("%d", "{}"))

        self.reset()

    def extendMarkdown(self, md):
        """ Add pieces to Markdown. """
        md.registerExtension(self)
        self.parser = md.parser
        self.md = md
        # Insert a `blockprocessor` before `ReferencePreprocessor`
        md.parser.blockprocessors.register(FootnoteBlockProcessor(self), 'footnote', 17)

        # Insert an inline pattern before `ImageReferencePattern`
        FOOTNOTE_RE = r'\[\^([^\]]*)\]'  # blah blah [^1] blah
        md.inlinePatterns.register(FootnoteInlineProcessor(FOOTNOTE_RE, self), 'footnote', 175)
        # Insert a tree-processor that would actually add the footnote div
        # This must be before all other tree-processors (i.e., `inline` and
        # `codehilite`) so they can run on the the contents of the div.
        md.treeprocessors.register(FootnoteTreeprocessor(self), 'footnote', 50)

        # Insert a tree-processor to reorder the footnotes if necessary. This must be after
        # `inline` tree-processor so it can access the footnote reference order
        # (`self.footnote_order`) that gets populated by the `FootnoteInlineProcessor`.
        if not self.getConfig("USE_DEFINITION_ORDER"):
            md.treeprocessors.register(FootnoteReorderingProcessor(self), 'footnote-reorder', 19)

        # Insert a tree-processor that will run after inline is done.
        # In this tree-processor we want to check our duplicate footnote tracker
        # And add additional `backrefs` to the footnote pointing back to the
        # duplicated references.
        md.treeprocessors.register(FootnotePostTreeprocessor(self), 'footnote-duplicate', 15)

        # Insert a postprocessor after amp_substitute processor
        md.postprocessors.register(FootnotePostprocessor(self), 'footnote', 25)

    def reset(self) -> None:
        """ Clear footnotes on reset, and prepare for distinct document. """
        self.footnote_order: list[str] = []
        self.footnotes: OrderedDict[str, str] = OrderedDict()
        self.unique_prefix += 1
        self.found_refs = {}
        self.used_refs = set()

    def unique_ref(self, reference: str, found: bool = False) -> str:
        """ Get a unique reference if there are duplicates. """
        if not found:
            return reference

        original_ref = reference
        while reference in self.used_refs:
            ref, rest = reference.split(self.get_separator(), 1)
            m = RE_REF_ID.match(ref)
            if m:
                reference = '%s%d%s%s' % (m.group(1), int(m.group(2))+1, self.get_separator(), rest)
            else:
                reference = '%s%d%s%s' % (ref, 2, self.get_separator(), rest)

        self.used_refs.add(reference)
        if original_ref in self.found_refs:
            self.found_refs[original_ref] += 1
        else:
            self.found_refs[original_ref] = 1
        return reference

    def findFootnotesPlaceholder(
        self, root: etree.Element
    ) -> tuple[etree.Element, etree.Element, bool] | None:
        """ Return ElementTree Element that contains Footnote placeholder. """
        def finder(element):
            for child in element:
                if child.text:
                    if child.text.find(self.getConfig("PLACE_MARKER")) > -1:
                        return child, element, True
                if child.tail:
                    if child.tail.find(self.getConfig("PLACE_MARKER")) > -1:
                        return child, element, False
                child_res = finder(child)
                if child_res is not None:
                    return child_res
            return None

        res = finder(root)
        return res

    def setFootnote(self, id: str, text: str) -> None:
        """ Store a footnote for later retrieval. """
        self.footnotes[id] = text

    def addFootnoteRef(self, id: str) -> None:
        """ Store a footnote reference id in order of appearance. """
        if id not in self.footnote_order:
            self.footnote_order.append(id)

    def get_separator(self) -> str:
        """ Get the footnote separator. """
        return self.getConfig("SEPARATOR")

    def makeFootnoteId(self, id: str) -> str:
        """ Return footnote link id. """
        if self.getConfig("UNIQUE_IDS"):
            return 'fn%s%d-%s' % (self.get_separator(), self.unique_prefix, id)
        else:
            return 'fn{}{}'.format(self.get_separator(), id)

    def makeFootnoteRefId(self, id: str, found: bool = False) -> str:
        """ Return footnote back-link id. """
        if self.getConfig("UNIQUE_IDS"):
            return self.unique_ref('fnref%s%d-%s' % (self.get_separator(), self.unique_prefix, id), found)
        else:
            return self.unique_ref('fnref{}{}'.format(self.get_separator(), id), found)

    def makeFootnotesDiv(self, root: etree.Element) -> etree.Element | None:
        """ Return `div` of footnotes as `etree` Element. """

        if not list(self.footnotes.keys()):
            return None

        div = etree.Element("div")
        div.set('class', 'footnote')
        etree.SubElement(div, "hr")
        ol = etree.SubElement(div, "ol")
        surrogate_parent = etree.Element("div")

        for index, id in enumerate(self.footnotes.keys(), start=1):
            li = etree.SubElement(ol, "li")
            li.set("id", self.makeFootnoteId(id))
            # Parse footnote with surrogate parent as `li` cannot be used.
            # List block handlers have special logic to deal with `li`.
            # When we are done parsing, we will copy everything over to `li`.
            self.parser.parseChunk(surrogate_parent, self.footnotes[id])
            for el in list(surrogate_parent):
                li.append(el)
                surrogate_parent.remove(el)
            backlink = etree.Element("a")
            backlink.set("href", "#" + self.makeFootnoteRefId(id))
            backlink.set("class", "footnote-backref")
            backlink.set(
                "title",
                self.getConfig('BACKLINK_TITLE').format(index)
            )
            backlink.text = FN_BACKLINK_TEXT

            if len(li):
                node = li[-1]
                if node.tag == "p":
                    node.text = node.text + NBSP_PLACEHOLDER
                    node.append(backlink)
                else:
                    p = etree.SubElement(li, "p")
                    p.append(backlink)
        return div


class FootnoteBlockProcessor(BlockProcessor):
    """ Find footnote definitions and store for later use. """

    RE = re.compile(r'^[ ]{0,3}\[\^([^\]]*)\]:[ ]*(.*)$', re.MULTILINE)

    def __init__(self, footnotes: FootnoteExtension):
        super().__init__(footnotes.parser)
        self.footnotes = footnotes

    def test(self, parent: etree.Element, block: str) -> bool:
        return True

    def run(self, parent: etree.Element, blocks: list[str]) -> bool:
        """ Find, set, and remove footnote definitions. """
        block = blocks.pop(0)

        m = self.RE.search(block)
        if m:
            id = m.group(1)
            fn_blocks = [m.group(2)]

            # Handle rest of block
            therest = block[m.end():].lstrip('\n')
            m2 = self.RE.search(therest)
            if m2:
                # Another footnote exists in the rest of this block.
                # Any content before match is continuation of this footnote, which may be lazily indented.
                before = therest[:m2.start()].rstrip('\n')
                fn_blocks[0] = '\n'.join([fn_blocks[0], self.detab(before)]).lstrip('\n')
                # Add back to blocks everything from beginning of match forward for next iteration.
                blocks.insert(0, therest[m2.start():])
            else:
                # All remaining lines of block are continuation of this footnote, which may be lazily indented.
                fn_blocks[0] = '\n'.join([fn_blocks[0], self.detab(therest)]).strip('\n')

                # Check for child elements in remaining blocks.
                fn_blocks.extend(self.detectTabbed(blocks))

            footnote = "\n\n".join(fn_blocks)
            self.footnotes.setFootnote(id, footnote.rstrip())

            if block[:m.start()].strip():
                # Add any content before match back to blocks as separate block
                blocks.insert(0, block[:m.start()].rstrip('\n'))
            return True
        # No match. Restore block.
        blocks.insert(0, block)
        return False

    def detectTabbed(self, blocks: list[str]) -> list[str]:
        """ Find indented text and remove indent before further processing.

        Returns:
            A list of blocks with indentation removed.
        """
        fn_blocks = []
        while blocks:
            if blocks[0].startswith(' '*4):
                block = blocks.pop(0)
                # Check for new footnotes within this block and split at new footnote.
                m = self.RE.search(block)
                if m:
                    # Another footnote exists in this block.
                    # Any content before match is continuation of this footnote, which may be lazily indented.
                    before = block[:m.start()].rstrip('\n')
                    fn_blocks.append(self.detab(before))
                    # Add back to blocks everything from beginning of match forward for next iteration.
                    blocks.insert(0, block[m.start():])
                    # End of this footnote.
                    break
                else:
                    # Entire block is part of this footnote.
                    fn_blocks.append(self.detab(block))
            else:
                # End of this footnote.
                break
        return fn_blocks

    def detab(self, block: str) -> str:
        """ Remove one level of indent from a block.

        Preserve lazily indented blocks by only removing indent from indented lines.
        """
        lines = block.split('\n')
        for i, line in enumerate(lines):
            if line.startswith(' '*4):
                lines[i] = line[4:]
        return '\n'.join(lines)


class FootnoteInlineProcessor(InlineProcessor):
    """ `InlineProcessor` for footnote markers in a document's body text. """

    def __init__(self, pattern: str, footnotes: FootnoteExtension):
        super().__init__(pattern)
        self.footnotes = footnotes

    def handleMatch(self, m: re.Match[str], data: str) -> tuple[etree.Element | None, int | None, int | None]:
        id = m.group(1)
        if id in self.footnotes.footnotes.keys():
            self.footnotes.addFootnoteRef(id)

            if not self.footnotes.getConfig("USE_DEFINITION_ORDER"):
                # Order by reference
                footnote_num = self.footnotes.footnote_order.index(id) + 1
            else:
                # Order by definition
                footnote_num = list(self.footnotes.footnotes.keys()).index(id) + 1

            sup = etree.Element("sup")
            a = etree.SubElement(sup, "a")
            sup.set('id', self.footnotes.makeFootnoteRefId(id, found=True))
            a.set('href', '#' + self.footnotes.makeFootnoteId(id))
            a.set('class', 'footnote-ref')
            a.text = self.footnotes.getConfig("SUPERSCRIPT_TEXT").format(footnote_num)
            return sup, m.start(0), m.end(0)
        else:
            return None, None, None


class FootnotePostTreeprocessor(Treeprocessor):
    """ Amend footnote div with duplicates. """

    def __init__(self, footnotes: FootnoteExtension):
        self.footnotes = footnotes

    def add_duplicates(self, li: etree.Element, duplicates: int) -> None:
        """ Adjust current `li` and add the duplicates: `fnref2`, `fnref3`, etc. """
        for link in li.iter('a'):
            # Find the link that needs to be duplicated.
            if link.attrib.get('class', '') == 'footnote-backref':
                ref, rest = link.attrib['href'].split(self.footnotes.get_separator(), 1)
                # Duplicate link the number of times we need to
                # and point the to the appropriate references.
                links = []
                for index in range(2, duplicates + 1):
                    sib_link = copy.deepcopy(link)
                    sib_link.attrib['href'] = '%s%d%s%s' % (ref, index, self.footnotes.get_separator(), rest)
                    links.append(sib_link)
                    self.offset += 1
                # Add all the new duplicate links.
                el = list(li)[-1]
                for link in links:
                    el.append(link)
                break

    def get_num_duplicates(self, li: etree.Element) -> int:
        """ Get the number of duplicate refs of the footnote. """
        fn, rest = li.attrib.get('id', '').split(self.footnotes.get_separator(), 1)
        link_id = '{}ref{}{}'.format(fn, self.footnotes.get_separator(), rest)
        return self.footnotes.found_refs.get(link_id, 0)

    def handle_duplicates(self, parent: etree.Element) -> None:
        """ Find duplicate footnotes and format and add the duplicates. """
        for li in list(parent):
            # Check number of duplicates footnotes and insert
            # additional links if needed.
            count = self.get_num_duplicates(li)
            if count > 1:
                self.add_duplicates(li, count)

    def run(self, root: etree.Element) -> None:
        """ Crawl the footnote div and add missing duplicate footnotes. """
        self.offset = 0
        for div in root.iter('div'):
            if div.attrib.get('class', '') == 'footnote':
                # Footnotes should be under the first ordered list under
                # the footnote div.  So once we find it, quit.
                for ol in div.iter('ol'):
                    self.handle_duplicates(ol)
                    break


class FootnoteTreeprocessor(Treeprocessor):
    """ Build and append footnote div to end of document. """

    def __init__(self, footnotes: FootnoteExtension):
        self.footnotes = footnotes

    def run(self, root: etree.Element) -> None:
        footnotesDiv = self.footnotes.makeFootnotesDiv(root)
        if footnotesDiv is not None:
            result = self.footnotes.findFootnotesPlaceholder(root)
            if result:
                child, parent, isText = result
                ind = list(parent).index(child)
                if isText:
                    parent.remove(child)
                    parent.insert(ind, footnotesDiv)
                else:
                    parent.insert(ind + 1, footnotesDiv)
                    child.tail = None
            else:
                root.append(footnotesDiv)


class FootnoteReorderingProcessor(Treeprocessor):
    """ Reorder list items in the footnotes div. """

    def __init__(self, footnotes: FootnoteExtension):
        self.footnotes = footnotes

    def run(self, root: etree.Element) -> None:
        if not self.footnotes.footnotes:
            return
        if self.footnotes.footnote_order != list(self.footnotes.footnotes.keys()):
            for div in root.iter('div'):
                if div.attrib.get('class', '') == 'footnote':
                    self.reorder_footnotes(div)
                    break

    def reorder_footnotes(self, parent: etree.Element) -> None:
        old_list = parent.find('ol')
        parent.remove(old_list)
        items = old_list.findall('li')

        def order_by_id(li) -> int:
            id = li.attrib.get('id', '').split(self.footnotes.get_separator(), 1)[-1]
            return (
                self.footnotes.footnote_order.index(id)
                if id in self.footnotes.footnote_order
                else len(self.footnotes.footnotes)
            )

        items = sorted(items, key=order_by_id)

        new_list = etree.SubElement(parent, 'ol')

        for index, item in enumerate(items, start=1):
            backlink = item.find('.//a[@class="footnote-backref"]')
            backlink.set("title", self.footnotes.getConfig("BACKLINK_TITLE").format(index))
            new_list.append(item)


class FootnotePostprocessor(Postprocessor):
    """ Replace placeholders with html entities. """
    def __init__(self, footnotes: FootnoteExtension):
        self.footnotes = footnotes

    def run(self, text: str) -> str:
        text = text.replace(
            FN_BACKLINK_TEXT, self.footnotes.getConfig("BACKLINK_TEXT")
        )
        return text.replace(NBSP_PLACEHOLDER, "&#160;")


def makeExtension(**kwargs):  # pragma: no cover
    """ Return an instance of the `FootnoteExtension` """
    return FootnoteExtension(**kwargs)
