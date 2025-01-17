import copy

import clique

from ayon_core.pipeline import (
    load,
    get_representation_context
)
from ayon_core.pipeline.load import get_representation_path_from_context
from ayon_core.lib.transcoding import IMAGE_EXTENSIONS

from ayon_openrv.api.pipeline import imprint_container
from ayon_openrv.api.ocio import (
    set_group_ocio_active_state,
    set_group_ocio_colorspace
)

import rv


class FramesLoader(load.LoaderPlugin):
    """Load frames into OpenRV"""

    label = "Load Frames"
    product_types = {"*"}
    representations = {"*"}
    extensions = {ext.lstrip(".") for ext in IMAGE_EXTENSIONS}
    order = 0

    icon = "code-fork"
    color = "orange"

    def load(self, context, name=None, namespace=None, data=None):

        filepath = self._format_path(context)
        # Command fails on unicode so we must force it to be strings
        filepath = str(filepath)

        # node_name = "{}_{}".format(namespace, name) if namespace else name
        namespace = namespace if namespace else context["folder"]["name"]

        loaded_node = rv.commands.addSourceVerbose([filepath])

        # update colorspace
        self.set_representation_colorspace(loaded_node,
                                           context["representation"])

        imprint_container(
            loaded_node,
            name=name,
            namespace=namespace,
            context=context,
            loader=self.__class__.__name__
        )

    def update(self, container, context):
        node = container["node"]

        filepath = self._format_path(context)
        filepath = str(filepath)
        repre_entity = context["representation"]

        # change path
        rv.commands.setSourceMedia(node, [filepath])

        # update colorspace
        self.set_representation_colorspace(node, context["representation"])

        # update name
        rv.commands.setStringProperty(node + ".media.name",
                                      ["newname"], True)
        rv.commands.setStringProperty(node + ".media.repName",
                                      ["repname"], True)
        rv.commands.setStringProperty(node + ".openpype.representation",
                                      [repre_entity["id"]], True)

    def remove(self, container):
        node = container["node"]
        group = rv.commands.nodeGroup(node)
        rv.commands.deleteNode(group)

    def _get_sequence_range(self, context):
        """Return frame range for image sequences.

        The start and end frame is based on the start frame and end frame of
        the representation or version documents. A single frame is never
        considered to be a sequence.

        Warning:
            If there are published sequences that do *not* have start and
            end frame data in the database then this will FAIL to detect
            it as a sequence.

        Args:
            context (dict): Representation context.

        Returns:
            Union[tuple[int, int], None]: (start, end) tuple if it is an
                image sequence otherwise it returns None.

        """
        repre_entity = context["representation"]

        # Only images may be sequences, not videos
        ext = repre_entity["context"].get("ext") or repre_entity["name"]
        if f".{ext}" not in IMAGE_EXTENSIONS:
            return

        repre_attribs = repre_entity["attrib"]
        # Frame range can be set on version or representation.
        # When set on representation it overrides version data.

        repre_frame_start = repre_attribs.get("frameStart")
        repre_frame_end = repre_attribs.get("frameEnd")
        if repre_frame_start is not None and repre_frame_end is not None:
            if repre_frame_start != repre_frame_end:
                return repre_frame_start, repre_frame_end
            # Single frame
            return

        # Fallback for image sequence that does not have frame start and frame
        # end stored in the database.
        # TODO: Maybe rely on rv.commands.sequenceOfFile instead?
        if "frame" in repre_entity["context"]:
            # Guess the frame range from the files
            files = repre_entity["files"]
            if len(files) > 1:
                paths = [f["path"] for f in files]
                collections, _remainder = clique.assemble(paths)
                if collections:
                    collection = collections[0]
                    frames = list(collection.indexes)
                    return frames[0], frames[-1]

    def _format_path(self, context):
        """Format the path with correct frame range.

        The openRV load command requires image sequences to be provided
        with `{start}-{end}#` for its frame numbers, for example:
            /path/to/sequence.1001-1010#.exr

        """

        sequence_range = self._get_sequence_range(context)
        if not sequence_range:
            return get_representation_path_from_context(context)

        context = copy.deepcopy(context)
        representation = context["representation"]
        if not representation["attrib"].get("template"):
            # No template to find token locations for
            return get_representation_path_from_context(context)

        def _placeholder(key):
            # Substitute with a long placeholder value so that potential
            # custom formatting with padding doesn't find its way into
            # our formatting, so that <f> wouldn't be padded as 0<f>
            return "___{}___".format(key)

        # We format UDIM and Frame numbers with their specific tokens. To do so
        # we in-place change the representation context data to format the path
        # with our own data
        start, end = sequence_range
        tokens = {
            "frame": f"{start}-{end}#",
        }
        has_tokens = False
        repre_context = representation["context"]
        for key, _token in tokens.items():
            if key in repre_context:
                repre_context[key] = _placeholder(key)
                has_tokens = True

        # Replace with our custom template that has the tokens set
        path = get_representation_path_from_context(context)

        if has_tokens:
            for key, token in tokens.items():
                if key in repre_context:
                    path = path.replace(_placeholder(key), token)

        return path

    def set_representation_colorspace(self, node, representation):
        colorspace_data = representation.get("data", {}).get("colorspaceData")
        if colorspace_data:
            colorspace = colorspace_data["colorspace"]
            # TODO: Confirm colorspace is valid in current OCIO config
            #   otherwise errors will be spammed from OpenRV for invalid space

            self.log.info(f"Setting colorspace: {colorspace}")
            group = rv.commands.nodeGroup(node)

            # Enable OCIO for the node and set the colorspace
            set_group_ocio_active_state(group, state=True)
            set_group_ocio_colorspace(group, colorspace)
