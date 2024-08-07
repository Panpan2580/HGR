import argparse
import json
import os
import sys
import time

import numpy as np
from func_timeout import func_timeout, FunctionTimedOut
from sympy import N
from tqdm import tqdm

from GeoDRL.converter import Text2Logic, Logic2Graph
from GeoDRL.logic_solver import LogicSolver
from agent.gen_vocab import reparse_graph_data
from reasoner.graph_matching import load_models_from_json, get_candidate_models_from_pool, match_graphs, get_model
from reasoner.logic_graph import GlobalGraph
from reasoner.graph_solver import GraphSolver
from reasoner.utils import dict_to_gml, draw_graph_from_gml, is_debugging
from reasoner.config import logger, eval_logger
from reasoner import config

with open(config.diagram_logic_forms_json_path, 'r') as diagram_file:
    diagram_logic_forms_json = json.load(diagram_file)
with open(config.text_logic_forms_json_path, 'r') as text_file:
    text_logic_forms_json = json.load(text_file)
with open(config.model_pool_path, 'r') as model_pool_file:
    model_pool, model_id_map = load_models_from_json(json.load(model_pool_file))


def get_logic_forms(q_id):

    text = diagram_logic_forms_json[str(q_id)]
    text["logic_forms"] = text.pop("diagram_logic_forms")
    text["logic_forms"].extend(text_logic_forms_json[str(q_id)]["text_logic_forms"])

    return text


def get_global_graph(parser, target, draw_graph=False):
    logger.debug("Target: %s", target)
    solver = LogicSolver(parser.logic)
    solver.initSearch()
    graph_dict = Logic2Graph(solver.logic, target)

    if draw_graph:
        graph_gml = dict_to_gml(graph_dict, False)
        draw_graph_from_gml(graph_gml)

    return GlobalGraph.from_dict(graph_dict)


def get_graph_solver(q_id):
    logic_forms = get_logic_forms(q_id)
    parser, target = Text2Logic(logic_forms)
    global_graph = get_global_graph(parser, target)
    graph_solver = GraphSolver(global_graph, model_pool)

    return graph_solver, target


def solve_with_model_sequence(q_id, model_id_list):
    res = {"id": q_id, "target": None, "answer": None, "model_instance_eq_num": None, "correctness": "no", "time": None}
    s_time = time.time()
    try:
        data_path = os.path.join(config.db_dir_single, str(q_id), "data.json")
        with open(data_path, "r") as f:
            data = json.load(f)
        candidate_value_list = data['precise_value']
        gt_id = ord(data['answer']) - 65  # 将A-D转换为0-3

        graph_solver, target = get_graph_solver(q_id)
        models = []
        for model_id in model_id_list:
            model = get_model(model_pool, model_id_map, model_id)
            models.append(model)
        graph_solver.solve_with_model_sequence(models)
        logger.debug("Target Node Value(s): %s", graph_solver.target_node_values)
        if len(graph_solver.target_node_values) > 0:
            target_node_values_float = [{key: N(value)} for d in graph_solver.target_node_values for
                                        key, value in d.items()]
            logger.debug("Target Node Value(s) (Float): %s", target_node_values_float)
        answer = graph_solver.answer

        res["model_instance_eq_num"] = graph_solver.model_instance_eq_num
        if answer is not None:
            if check_answer(answer, candidate_value_list, gt_id):
                res["correctness"] = "yes"
            else:
                # 可能需要将弧度转换成度数后再验证答案
                answer_degrees = np.degrees(float(answer))
                if check_answer(answer_degrees, candidate_value_list, gt_id):
                    res["correctness"] = "yes"
                    answer = answer_degrees
                elif check_answer(360 - answer_degrees, candidate_value_list, gt_id):
                    res["correctness"] = "yes"
                    answer = 360 - answer_degrees

        res["target"] = target
        res["answer"] = answer
        logger.debug("Answer: %s", answer)
        res['time'] = str(time.time() - s_time)
    except Exception as e:
        logger.error(e)
        res['time'] = str(time.time() - s_time)
        return res

    return res


def solve_question(q_id):
    res = {"id": q_id, "target": None, "answer": None, "model_instance_eq_num": None, "correctness": "no", "time": None}
    s_time = time.time()
    try:
        data_path = os.path.join(config.db_dir_single, str(q_id), "data.json")
        with open(data_path, "r") as f:
            data = json.load(f)
        candidate_value_list = data['precise_value']
        gt_id = ord(data['answer']) - 65  # 将A-D转换为0-3

        graph_solver, target = get_graph_solver(q_id)
        graph_solver.solve()
        logger.debug("Total Rounds: %s", graph_solver.rounds)
        logger.debug("Target Node Value(s): %s", graph_solver.target_node_values)
        if len(graph_solver.target_node_values) > 0:
            target_node_values_float = [{key: N(value)} for d in graph_solver.target_node_values for
                                        key, value in d.items()]
            logger.debug("Target Node Value(s) (Float): %s", target_node_values_float)
        answer = graph_solver.answer

        res["model_instance_eq_num"] = graph_solver.model_instance_eq_num
        if answer is not None:
            if check_answer(answer, candidate_value_list, gt_id):
                res["correctness"] = "yes"
            else:
                # 可能需要将弧度转换成度数后再验证答案
                answer_degrees = np.degrees(float(answer))
                if check_answer(answer_degrees, candidate_value_list, gt_id):
                    res["correctness"] = "yes"
                    answer = answer_degrees
                elif check_answer(360 - answer_degrees, candidate_value_list, gt_id):
                    res["correctness"] = "yes"
                    answer = 360 - answer_degrees

        res["target"] = target
        res["answer"] = answer
        logger.debug("Answer: %s", answer)
        res['time'] = str(time.time() - s_time)
    except Exception as e:
        logger.error(e)
        res['time'] = str(time.time() - s_time)
        return res

    return res


def evaluate_all_questions(st, ed):
    with open(config.error_ids_path, 'r') as file:
        error_ids = {int(line.strip()) for line in file}  # 确保错误ID是整数

    # 生成所有题目ID并排除错误ID
    all_question_ids = set(range(st, ed))
    valid_question_ids = list(all_question_ids - error_ids)  # 将集合转换为列表
    total = len(valid_question_ids)
    removed_count = len(all_question_ids) - total

    print(f"Removed {removed_count} questions with parsing errors.")

    correct = 0
    solved = 0
    st_time = time.time()
    result_json_dict = {}

    for q_id in tqdm(valid_question_ids):
        try:
            # 设置超时时间为60秒
            res = func_timeout(120, solve_question, args=(q_id,))
        except FunctionTimedOut:
            logger.error(f"Error occurred while solving question {q_id}: FunctionTimedOut.")
            continue
        except Exception as e:
            logger.error(f"Error occurred while solving question {q_id}: {e}")
            continue

        if res:
            for k, v in res.items():
                res[k] = str(v)
            if res['answer'] is not None:
                solved += 1

                if res['correctness'] == "yes":
                    result_json_dict[res["id"]] = res
                    correct += 1
            eval_logger.debug(res)
        else:
            logger.error(f"Error occurred while solving question {q_id}.")

    ed_time = time.time()

    print(f"Total: {total}, Solved: {solved}, Correctness: {correct}, CorrectRate: {correct * 1.0 / total}")
    print(f"Time Cost: {ed_time - st_time} seconds.")
    with open('correct_' + str(correct) + '.json', 'w') as outfile:
        json.dump(result_json_dict, outfile, indent=4)


def check_answer(answer, candidate_value_list, gt_id):
    if answer is None:
        return False
    try:
        if (all([x is not None for x in candidate_value_list]) and
                abs(float(candidate_value_list[gt_id]) - answer) == min([abs(float(x) - answer)
                                                                         for x in candidate_value_list])):
            return True
    except Exception as e:
        logger.error(e)
    return False


def test_graph_matching(q_id):
    logic_forms = get_logic_forms(q_id)
    parser, target = Text2Logic(logic_forms)
    global_graph = get_global_graph(parser, target)
    with open(config.model_pool_test_path, 'r') as model_pool_file:
        model_pool, _ = load_models_from_json(json.load(model_pool_file))

    candidate_models = get_candidate_models_from_pool(model_pool, global_graph)
    for model in candidate_models:
        relations = []
        mapping_dict_list = match_graphs(model, global_graph)
        for mapping_dict in mapping_dict_list:
            relation = model.generate_relation(mapping_dict)
            if relation not in relations:
                relations.append(relation)
                print(mapping_dict)
                print(model.generate_relation(mapping_dict))


def test_draw_global_graph(q_id):
    logic_forms = get_logic_forms(q_id)
    parser, target = Text2Logic(logic_forms)
    _ = get_global_graph(parser, target, True)


def check_id_in_error_ids(question_id, error_file):
    with open(error_file, 'r') as file:
        error_ids = {line.strip() for line in file}

    if str(question_id) in error_ids:
        return True
    else:
        return False


def test_one_question(q_id):
    if check_id_in_error_ids(q_id, config.error_ids_path):
        logger.error(f"Error: question id {q_id} is in error_ids")
        sys.exit(1)

    if is_debugging():
        res = solve_question(q_id)
        logger.debug(res)
    else:
        try:
            # 设置超时时间为60秒
            res = func_timeout(120, solve_question, args=(q_id,))
            logger.debug(res)
        except FunctionTimedOut:
            logger.error(f"Error: solve_question timed out")


def test_reparse_graph_data(q_id):
    graph_solver, _ = get_graph_solver(q_id)
    graph_dict = graph_solver.global_graph.to_dict()
    print(graph_dict['node_attr'])
    graph_data, map_dict = reparse_graph_data(graph_dict, {})
    print(graph_data['node_attr'])
    print(map_dict)


def test_solve_with_model_sequence(q_id, model_id_list):
    if check_id_in_error_ids(q_id, config.error_ids_path):
        logger.error(f"Error: question id {q_id} is in error_ids")
        sys.exit(1)

    if is_debugging():
        res = solve_with_model_sequence(q_id, model_id_list)
        logger.debug(res)
    else:
        try:
            # 设置超时时间为60秒
            res = func_timeout(120, solve_with_model_sequence, args=(q_id, model_id_list,))
            logger.debug(res)
        except FunctionTimedOut:
            logger.error(f"Error: solve_question timed out")


if __name__ == "__main__":
    # 测试多个题目
    # evaluate_all_questions(0, 10)

    parser = argparse.ArgumentParser(description="Solve a specific question by number.")
    parser.add_argument('question_id', type=int, help='The id of the question to solve')
    try:
        args = parser.parse_args()
        q_id = args.question_id

        # 测试解答单个题目
        # test_one_question(q_id)

        # 测试模型匹配
        # test_graph_matching(q_id)

        # 绘制全局图
        # test_draw_global_graph(q_id)

        # test_reparse_graph_data(q_id)

        test_solve_with_model_sequence(q_id, [45, 53])
    except argparse.ArgumentError:
        logger.error("Error: question id is required")
        parser.print_help()
        sys.exit(1)
