"""
    File name: tools
    Author: Artelys
    Creation date: 08/01/2019
    Python Version: 3.6
"""
import numpy as np
import networkx as nx
import geopandas as gpd
from shapely.geometry import Point, LineString
from geonetworkx.geograph import GeoGraph
import geonetworkx.settings as settings
from geonetworkx.geometry_operations import get_closest_line_from_points, split_line
from geonetworkx.utils import get_new_node_unique_name, euclidian_distance
from geonetworkx.readwrite import graph_nodes_to_gdf
from collections import defaultdict


def spatial_points_merge(graph: GeoGraph, points_gdf: gpd.GeoDataFrame, inplace=True, merge_direction="both") -> GeoGraph:
    """
    Merge given points as node with a spatial merge.
    :param graph: A GeoGraph or derived class describing a spatial graph.
    :param points_gdf: A list of point describing new nodes to add. Points are projected on the closest edge of the
    graph and an intersection node is added if necessary.
    :param inplace: If True, do operation inplace and return None.
    :param merge_direction: For directed graphs:
         * `both`: 2 edges are added: graph -> new node and new node -> graph
         * `in`: 1 edge is added: new_node -> graph
         * `out`: 1 edge is added: graph -> new_node
    :return: None if inplace, new graph otherwise.
    """
    if not inplace:
        graph = graph.copy()
    # 1. Find closest edge for each point
    edges_as_lines = nx.get_edge_attributes(graph, graph.edges_geometry_key)
    points = points_gdf[settings.GPD_GEOMETRY_KEY]
    points_coords = np.array([[p.x, p.y] for p in points])
    lines_indexes = get_closest_line_from_points(points_coords, edges_as_lines.values())
    edges_to_split = defaultdict(dict)
    # Add node, intersection node and edge (node, intersection node)
    for p, p_index, point in zip(range(len(points_gdf)), points_gdf.index, points_gdf[settings.GPD_GEOMETRY_KEY]):
        # 1.1 Add given node
        node_name = get_new_node_unique_name(graph, p_index)
        node_info = {c: points_gdf.at[p_index, c] for c in points_gdf.columns}
        node_info[graph.x_key] = point.x
        node_info[graph.y_key] = point.y
        graph.add_node(node_name, **node_info)
        # 1.2 Add projected node if necessary
        closest_edge_name = list(edges_as_lines.keys())[lines_indexes[p]]
        closest_line = edges_as_lines[closest_edge_name]
        closest_line_length = closest_line.length
        intersection_distance_on_line = closest_line.project(point)
        # if the intersection point is on the edge
        if 0 < intersection_distance_on_line < closest_line_length:
            projected_point = closest_line.interpolate(intersection_distance_on_line)
            intersection_node_name = get_new_node_unique_name(graph, settings.INTERSECTION_PREFIX + str(p_index))
            intersection_node_info = {graph.x_key: projected_point.x, graph.y_key: projected_point.y}
            graph.add_node(intersection_node_name, **intersection_node_info)
            # Store line to modify
            edges_to_split[closest_edge_name][intersection_node_name] = intersection_distance_on_line
        else:  # if the intersection point is on of the two edge extremities
            first_node = closest_edge_name[0]
            first_node_point = Point([graph.nodes[first_node][graph.x_key], graph.nodes[first_node][graph.y_key]])
            second_node = closest_edge_name[1]
            second_node_point = Point([graph.nodes[second_node][graph.x_key], graph.nodes[second_node][graph.y_key]])
            distance_to_first_extremity = euclidian_distance(point, first_node_point)
            distance_to_second_extremity = euclidian_distance(point, second_node_point)
            if distance_to_first_extremity < distance_to_second_extremity:
                intersection_node_name = first_node
            else:
                intersection_node_name = second_node
        # 1.3 Add edge : node <-> intersection_node
        in_edge_data = {graph.edges_geometry_key: LineString([graph.get_node_coordinates(node_name),
                                                              graph.get_node_coordinates(intersection_node_name)])}
        if graph.is_directed():
            out_edge_data = {graph.edges_geometry_key: LineString([graph.get_node_coordinates(intersection_node_name),
                                                                   graph.get_node_coordinates(node_name)])}
            if merge_direction == "both":
                graph.add_edge(node_name, intersection_node_name, **in_edge_data)
                graph.add_edge(intersection_node_name, node_name, **out_edge_data)
            elif merge_direction == "in":
                graph.add_edge(node_name, intersection_node_name, **in_edge_data)
            else:  # "out"
                graph.add_edge(intersection_node_name, node_name, **out_edge_data)
        else:
            graph.add_edge(node_name, intersection_node_name, **in_edge_data)
    # 2. Split edges where a node have been projected
    for e in edges_to_split:
        intersection_nodes = edges_to_split[e]
        if len(intersection_nodes) > 0:
            initial_line = edges_as_lines[e]
            # 2.1 remove initial edge
            if graph.has_edge(*e):
                graph.remove_edge(*e)
            # 2.2 cut the initial line
            sorted_intersection_nodes = sorted(intersection_nodes.keys(), key=lambda n: intersection_nodes[n])
            distances_on_initial_line = [intersection_nodes[n] for n in sorted_intersection_nodes]
            split_lines = []
            cut_lines = split_line(initial_line, distances_on_initial_line[0])
            split_lines.append(cut_lines[0])
            for i in range(len(sorted_intersection_nodes) - 1):
                cut_lines = split_line(cut_lines[1], distances_on_initial_line[i + 1] - distances_on_initial_line[i])
                split_lines.append(cut_lines[0])
            split_lines.append(cut_lines[1])
            # 2.2 add intermediary edges
            first_edge_data = {graph.edges_geometry_key: split_lines[0]}
            graph.add_edge(e[0], sorted_intersection_nodes[0], **first_edge_data)
            last_edge_data = {graph.edges_geometry_key: split_lines[-1]}
            graph.add_edge(sorted_intersection_nodes[-1], e[1], **last_edge_data)
            for i in range(len(sorted_intersection_nodes) - 1):
                edge_data = {graph.edges_geometry_key: split_lines[i + 1]}
                graph.add_edge(sorted_intersection_nodes[i], sorted_intersection_nodes[i + 1], **edge_data)
    if not inplace:
        return graph
    return None


def spatial_graph_merge(base_graph: GeoGraph, other_graph: GeoGraph,
                        inplace=True, merge_direction="both",
                        unmerged_nodes = [], merging_nodes = []):
    """
    Operates spatial merge between two graphs. Spatial edge projection is used on merging nodes.
    :param base_graph: Base graph on which the merge operation is done.
    :param other_graph: Input graph to merge. Modified graph if operation is done inplace.
    :param inplace: If True, do operation inplace and return None.
    :param merge_direction: See `spatial_points_merge`
    :param unmerged_nodes: List of unmerged nodes among `other_graph` nodes.
    :param merging_nodes: List of merging nodes among `other_graph` nodes.
    :return: A new graph with the same type as `base_graph` if not inplace.
    """
    if base_graph.is_directed() != other_graph.is_directed():
        raise ValueError("Merging a directed graph and an undirected graph is ambiguous")
    if base_graph.is_multigraph() != other_graph.is_multigraph():
        raise ValueError("Merging a multigraph and a graph is ambiguous")
    if len(unmerged_nodes) > 0 and len(merging_nodes) > 0:
        raise ValueError("Cannot provide `unmerged_nodes` and `merging_nodes`")
    if len(merging_nodes) > 0:
        other_graph_view = nx.subgraph(other_graph, merging_nodes)
        nodes_gdf = graph_nodes_to_gdf(other_graph_view)
    else:
        nodes_gdf = graph_nodes_to_gdf(other_graph)
        nodes_gdf.drop(index=unmerged_nodes, inplace=True)
    if inplace:
        spatial_points_merge(base_graph, nodes_gdf, inplace=inplace, merge_direction=merge_direction)
        merged_graph = base_graph
    else:
        merged_graph = spatial_points_merge(base_graph, nodes_gdf, inplace=inplace, merge_direction=merge_direction)
    merged_graph = nx.compose(merged_graph, other_graph)
    if not inplace:
        return merged_graph


