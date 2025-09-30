import os
import traceback
import json
import time

import sys
print (sys.path)

from tosh_teach.dataset.dataset import Dataset
from tosh_teach.logger import create_logger
from tosh_teach.simulators import simulator_factory
from tosh_teach.simulators.simulator_THOR import TEAChController # TODO: we may want to abstract from the specific simulator in the future

from play_game import GamePlayer

from tosh_teach.logger import create_logger
from typing import List
from driver.mysledmap.mapper.env.teach.neural_symbolic_state_tracker import NeuralSymbolicAgentStateTracker

from driver.state_tracker import state_tracker_static_obj

from commander.scripted_commander import ScriptedCommander
import re
from enum import Enum
import subgoal_tracker
import action_utils as utils
from executor import Executor

from arguments import args
from driver.planner.interactive_planner  import InteractiveStatefulPlanner

import driver.constants as constants

import logging
#logging.basicConfig(level=logging.ERROR)
logging.getLogger("openai").setLevel(logging.ERROR)  # Or logging.WARNING
logging.getLogger("httpx").setLevel(logging.ERROR) 
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("tosh_teach").setLevel(logging.ERROR) 

logger = create_logger(__name__)
simulator_name = "thor"
gpu_id = 0
#os.environ['DISPLAY']=':9'
#os.environ['DISPLAY']=':9'
x_display = f"{os.environ['DISPLAY'].split(':')[1]}.{gpu_id}"
simulator_options = {}

VERSION='1.6.1' # for identifying log

#added for comms between web interface and embodiedAI - callum
MSG_RE = re.compile(r'^MSGID:(?P<id>[^|]+)\|(?P<text>.*)$')



class UserAct(Enum):
    # these commands are handled rule-based
    OBJECTS = 0
    CURRENT_PLAN = 1
    HISTORY = 2
    RESET_ALL = 3
    RESET_PLAN = 4
    RESET_OBJS = 5
    ########## commands above should not lead to execution of the next subgoal
    CONFIRM_POSITIVE = 7
    SKIP = 8 # do not use the last example for annotation
    # anything not matching rule-based NLU is passed to LLM
    # TODO: more user acts to be handles by the LLM
    COMMAND = 9
    TASK = 10
    VISIBLE = 11
    UNKNOWN = 12

class SystemAct(Enum):
    CONFIRM_ACTION = 0
    CONFIRM_PLAN = 1
    EXECUTE = 2
    REQUEST_INSTRUCTION = 3
    PROVIDE_INFO = 4
    ASK_HELP = 5


class InteractiveRunner:

    def __init__(self, data_dir, game_id, split='valid_unseen' ):
        assert args.planning_mode == 'interactive' 
        self.runner = GamePlayer()
        self.data_dir = data_dir
        self.game_id = game_id
        #self.split = split
        self.runner.init_game(data_dir=self.data_dir, game_id=self.game_id)

        self.commanderModel = None
        if args.commander_mode=='scripted':
            self.commanderModel = ScriptedCommander(fname=args.commander_script)

        self.utt_index = 0
        self.game_start_time = time.strftime("%Y%m%d-%H%M%S")
        
        # regular expressions for direct commands
        self.confirm_re = re.compile('(|y|yes|yeah|sure|ok)$', re.IGNORECASE)
        #question_re = re.compile('\?',  re.IGNORECASE)
        self.command_re = re.compile('\w+\(.+\)',  re.IGNORECASE) # match command(args)    
        self.resetall_re = re.compile('resetall$',  re.IGNORECASE)  # reset the whole game (reloads env)
        self.resetplan_re = re.compile('resetplan$',  re.IGNORECASE)  # reset the plan
        self.resetobjs_re = re.compile('resetobj$',  re.IGNORECASE)  # reset the object list
        self.objects_re = re.compile('objects$',  re.IGNORECASE)  # get currently discussed objects
        self.task_re = re.compile('task$',  re.IGNORECASE)  # get currently discussed objects
        self.history_re = re.compile('history$',  re.IGNORECASE)  # get last state
        self.currentplan_re = re.compile('plan$',  re.IGNORECASE)  #get current plan
        self.skip_re = re.compile('skip$',  re.IGNORECASE)  #get current plan
        self.visible_re = re.compile('visible$',  re.IGNORECASE)  #get current plan
        # for logging - to enable linking between logs
        self.global_timer = ''
        #added for web comms - callum
        self.current_msg_id = None  # correlation id for the current user turn

    def get_UserAct(self, utt):
        '''
        intent is command, query, social
        TODO: plug NLU component here
        '''  

        if self.resetall_re.match(utt):
            return UserAct.RESET_ALL
            
        if self.resetplan_re.match(utt):
            return UserAct.RESET_PLAN               

        if self.resetobjs_re.match(utt):
            return UserAct.RESET_OBJS   

        if self.confirm_re.match(utt):
            return UserAct.CONFIRM_POSITIVE

        if self.objects_re.match(utt):
            return UserAct.OBJECTS
        
        if self.task_re.match(utt):
            return UserAct.TASK        

        if self.history_re.match(utt):
            return UserAct.HISTORY   

        if self.skip_re.match(utt):
            return UserAct.SKIP        

        if self.visible_re.match(utt):
            return UserAct.VISIBLE        
        
        if self.currentplan_re.match(utt):
            return UserAct.CURRENT_PLAN        
        
        if self.command_re.match(utt):
            return UserAct.COMMAND
        
        return UserAct.UNKNOWN

    def generate_nl_response(self, llm_plan):
        plan = llm_plan.split("_")
        assert(len(plan)>0)
        assert(plan[0]=='Communicate')
        if plan[1]=='Objects':
            # print info about objects of interest
            print(f'ROBOT> Objects of interest include {", ".join(list(state_tracker_static_obj.objects_of_interest_byllmid.keys()))}' )
        elif plan[1]=='Location':
            # print current position
            if self.runner.controller.last_event and 'agent' in self.runner.controller.last_event.metadata:
                print(f'ROBOT> My current pose in sim: {self.runner.controller.last_event.metadata["agent"]["position"]}')
            if state_tracker_static_obj.neuro_symbolic_env_state_tracker:
                print(f'ROBOT> My current pose in map: {state_tracker_static_obj.neuro_symbolic_env_state_tracker.symbolic_world_repr.agent_pose_m}')
        elif plan[1]=='Social':
            print(f'ROBOT> TODO: call LLM with a social interaction prompt')


    def execute(self, subgoal):
        '''
        input subgoal object
        '''

        try: 
            
            #SS: this had to be introduced after executor was changed to accomodate object search. not ideal solution, but should do   
            target_obj = None
            rec_obj = None
            
            # if action is Motion, do  nothing with params
            if subgoal.subgoal_action!='Motion' and subgoal.subgoal_action!='Find':   

                if subgoal.object_id is not None and subgoal.object_id != '' and  \
                subgoal.object_type is not None and subgoal.object_type != '':
                    target_obj, error_message = self.runner.executor.search(subgoal.object_id, subgoal.object_type)
                    if target_obj is None:
                        subgoal.set_status(False)
                        subgoal.failure_reason = error_message
                        return False


                if target_obj is not None and subgoal.receptacle_id is not None and subgoal.receptacle_id != '' and  \
                subgoal.receptacle_type is not None and subgoal.receptacle_type != '':
                    rec_obj, error_message = self.runner.executor.search(subgoal.receptacle_id, subgoal.receptacle_type)
                    if rec_obj is None:
                        subgoal.set_status(False)
                        subgoal.failure_reason = error_message
                        return False
                

            success, error_message, help_message = subgoal.execute(self.runner.executor, 
                                                                   target_obj=target_obj,
                                                                   rec_obj = rec_obj)
            
            self.error_messages_log.append({'subgoal':subgoal.get_summary(), 'err':error_message})
            
            if success==True: # do we want to print this after every message?
                self.print_command(f'ROBOT> I succeeded to {subgoal.get_summary()}')
            else:
                self.print_command(f'ROBOT> Sorry, I failed to {subgoal.get_summary()}')

            #if error_message is not None and error_message!= '':
            #    self.print_command('ROBOT> ' + str(error_message))
            #if help_message is not None and help_message!= '':
            #        self.print_command('ROBOT> ' + str(help_message))
        except Exception as e:
            self.print_command(traceback.format_exc()) # TODO: print this to the log
            self.print_command (f"ROBOT> something went wrong when executing the command, please check the log for more info")
            success = False
            subgoal.status = constants.SubgoalStatus.FAILED
            subgoal.failure_reason = str(e)
            self.exceptions_log.append(str(e)) 
 
        return success

    def get_last_element(self, arr):
        if len(arr)==0: return ''
        return arr[len(arr)-1]

    def get_last_user_act(self):
        return self.get_last_element(self.user_acts)
    
    def get_last_sys_act(self):
        return self.get_last_element(self.system_acts)  
    
    def get_context(self):
        return {
        'obj': state_tracker_static_obj
        }

    def dump_state(self, utt, old_plan, new_plan, llm_message):
        #TODO: add context

        j  = {'index': self.utt_index,'game': args.use_environment, 'context': state_tracker_static_obj.get_context_for_llm(), 'prev_sys_act': str(self.get_last_sys_act()), 
              'utt': utt, 'old_plan': old_plan, 'new_plan': new_plan, 'llm_raw': llm_message}

        j['version'] = VERSION
        j['game_start'] = self.game_start_time # to use in logs as game id
        j['utt_time'] = self.global_timer
        with open(args.teach_examples_output, 'a') as file:
            file.write(json.dumps(j) + '\n')

        if args.teach_examples_savemetadata is True:
            self.save_metadata(j['game_start'], j['utt_time'])   


    def dump_correction(self, utt):

        j  = {'index': self.utt_index, 'utt': utt, 'prev_sys_act': str(self.get_last_sys_act()), 'prev_usr_act': str(self.get_last_user_act())}

        j['version'] = VERSION
        j['game_start'] = self.game_start_time # to use in logs as game id
        j['utt_time'] = self.global_timer
        with open(args.teach_examples_output, 'a') as file:
            file.write(json.dumps(j) + '\n') 
         
    def save_metadata(self, game_start, utt_time):
        '''
        writes the status of all current objects into a file
        get directory from args.teach_examples_output
        subdirectory by game start time (so all otputs for each game are in the same dir)
        filename utt_time
        '''
        try:
            dirname = args.teach_examples_output[:args.teach_examples_output.rfind('/')]
            if not os.path.exists(dirname + '/' + game_start):
                os.makedirs(dirname + '/' + game_start)
                print('made directory for logging states')

            with open(dirname + '/' + game_start + '/' + utt_time + '.json', 'w') as f:
                json.dump(self.runner.controller.last_event.metadata, f, indent=4)

        except Exception as ex:
            print('Failed to save metadata: ' + ex)



    def print_command(self, text, to_stdout=True, to_logfile=True, msg_id=None):
        """
        Prints a line to stdout and logs it. For TFEFR integration we:
          - Prefix assistant-visible lines with [OUT][<id>]
          - Emit a planner snapshot as [PLAN][<id>] (still logging to file as before)
        """
        formatted_text = text.replace('\\n', '\n')
        tag = msg_id or getattr(self, "current_msg_id", None)

        if to_stdout:
            if tag:
                # Assistant-visible line routed by the gateway to the correct client
                print(f"[OUT][{tag}] {formatted_text}", flush=True)
                # Live plan snapshot for UI (optional but helpful)
                try:
                    plan = self.runner.planner.get_summary(showstatus=True)
                    print(f"[PLAN][{tag}] {plan}", flush=True)
                except Exception:
                    # Never let plan formatting break stdout
                    pass
            else:
                # Fallback (no correlation id); still flush to keep latency low
                print(formatted_text, flush=True)

        if to_logfile:
            with open(args.teach_examples_output + '_' + self.game_start_time + '_utts.log', 'a') as file:
                file.write(self.global_timer + " " + json.dumps(formatted_text) + '\n')
                try:
                    file.write(self.global_timer + "<PLAN>" + self.runner.planner.get_summary(showstatus=True) + '\n')
                except Exception:
                    pass

   
    def run_interactive(self):
        '''
        Use InteractiveStatefulPlanner
        maintain the current plan after each step, notify the user
        if user input is empty, continue with the current plan
        '''
        no_pause_subgoals = ['Find', 'Go_to']
        assert type(self.runner.planner)==InteractiveStatefulPlanner
        self.error_messages_log = []
        self.exceptions_log = []

        if args.use_gt_all is not True:
            self.runner.init_explore()

        utt = ""
        self.system_acts = []
        self.user_acts = []

        # print to the log the instruction and the game id
        self.print_command(args.use_environment, to_stdout=False)
        self.print_command(self.runner.planner.ind_interpreter.sys_instruction, to_stdout=False)

        # for logging
        self.runner.planner =  InteractiveStatefulPlanner(self.runner.executor, args.teach_examples_output + '_' + self.game_start_time + '_llm.log') 
 
        while utt!='exit':

            
            
            # This is NLG. RQ: use model to decide whether to confirm and how to phrase a response
            # process the curremt subgoal of the planner
            # if there are no current subgoals ask what to do
            # if the current subgoal has status FAILED, ask user for help
            # if the current subgoal has status NOTATTEMPTED, 
            #   if  subgoal is in NOT the list of no-confirm, check with user
            #   else execute the subgoal
            next_subgoal = self.runner.planner.get_next_subgoal()
            # if the last user act was a debug command, do not try to execute
            #if not (self.get_last_user_act()!=UserAct.COMMAND or self.get_last_user_act()!=UserAct.CONFIRM_POSITIVE or self.get_last_user_act()==UserAct.UNKNOWN):
            #    pass
            
            if not (self.get_last_user_act()==UserAct.COMMAND or self.get_last_user_act()==UserAct.CONFIRM_POSITIVE or self.get_last_user_act()==UserAct.UNKNOWN):
                pass
            # if the last user utt was to ask LLM 
            elif self.get_last_user_act()==UserAct.UNKNOWN:
                # if there is no subgoal, assume planning failed; o/w confirm new plan
                if next_subgoal==None:
                    self.print_command ('ROBOT> I failed to interpret instruction. Please provide a correction or a new instruction.')
                    self.system_acts.append(SystemAct.REQUEST_INSTRUCTION)
                else:
                    self.print_command (f'ROBOT> Here is a new plan: {self.runner.planner.get_summary()}.  Is this right?')
                    self.system_acts.append(SystemAct.CONFIRM_PLAN)
            elif next_subgoal==None:
                self.print_command ('ROBOT> No goals. What should I do next?')
                self.system_acts.append(SystemAct.REQUEST_INSTRUCTION)
            elif next_subgoal.status==constants.SubgoalStatus.FAILED:
                self.print_command(f'ROBOT> I failed to execute {next_subgoal.get_summary()} because {next_subgoal.failure_reason}. Help!')
                self.system_acts.append(SystemAct.ASK_HELP)
            # if the action is not yet confirmed and the setting is to pause
            elif args.pause_every_subgoal and next_subgoal.subgoal_action not in no_pause_subgoals and not next_subgoal.is_confirmed:
                self.print_command(f'ROBOT> I am going to do this now: {next_subgoal.get_summary()}. Enter or type another command.')
                self.system_acts.append(SystemAct.CONFIRM_ACTION)
            else: # execute subgoal and restart the loop - we are not looking for the user input
                #after a physical action   
                self.system_acts.append(SystemAct.EXECUTE)
                self.execute(next_subgoal)
                continue

                
            # this is NLU
            # if we are running with human user, wait for the intput on command line
            # else get input from the commander model
            if args.commander_mode == 'human':
                print('USER> ', end='')
                self.global_timer = time.strftime("%Y%m%d-%H%M%S")
                raw = input()
                msg_id = None
                m = MSG_RE.match(raw)
                if m:
                    msg_id = m.group("id")
                    utt = m.group("text")
                else:
                    utt = raw
                self.current_msg_id = msg_id  # carry it for this turn’s responses
                self.print_command(f'USER>{utt}', to_stdout=False, msg_id=msg_id)
            else:
                self.global_timer = time.strftime("%Y%m%d-%H%M%S")
                utt = self.commanderModel.get_utt()
                self.print_command (f'USER>{utt}')
                
            intent = self.get_UserAct(utt)
            # handle keywords that should not be passed to LLM
            self.user_acts.append(intent)
            if intent==UserAct.RESET_ALL:
                self.print_command('ROBOT> Resetting the environment')
                self.runner.init_game(data_dir=self.data_dir, game_id=self.game_id)
                self.utt_index = 0
                self.game_start_time = time.strftime("%Y%m%d-%H%M%S")
                state_tracker_static_obj.objects_of_interest_byllmid = {}
                continue
            
            if intent==UserAct.RESET_PLAN:
                self.print_command('ROBOT> Resetting the plan ')
                #self.runner.planner.reset()
                # this will reread the prompts and erase all history
                self.runner.planner =  InteractiveStatefulPlanner(self.runner.executor, args.teach_examples_output + '_' + self.game_start_time + '_llm.log') 
                continue      

            if intent==UserAct.RESET_OBJS:
                self.print_command('ROBOT> Resetting the objects ')
                #self.runner.planner.reset()
                # this will reread the prompts and erase all history
                state_tracker_static_obj.objects_of_interest_byllmid = {}
                continue    

            if intent==UserAct.CONFIRM_POSITIVE:
                if self.get_last_sys_act()==SystemAct.CONFIRM_ACTION:
                    next_subgoal.set_confirmed()
                    self.print_command('ROBOT> Executing...')
                elif self.get_last_sys_act()==SystemAct.CONFIRM_PLAN:
                    self.runner.planner.set_confirmed()
                    # save the plan that was confirmed
                    self.dump_correction(utt)
                    self.print_command('ROBOT> OK, going to execute the plan ')
                else:
                    self.print_command('ROBOT> I think you confirmed something... ')
                continue         

            if intent==UserAct.OBJECTS:
                #obj_of_interest = list(state_tracker_static_obj.objects_of_interest_byllmid.keys())
                self.print_command(f"ROBOT> We have discussed these objects: " +
                                   ", ".join(state_tracker_static_obj.get_objects_of_interest_summary()) + 
                                   #f"\nIDs:{json.dumps(state_tracker_static_obj.objects_of_interest_byllmid)}."
                                   f". I am holding {state_tracker_static_obj.get_held_object(return_llmid=True)}.")
                continue

            if intent==UserAct.VISIBLE:
                #obj_of_interest = list(state_tracker_static_obj.objects_of_interest_byllmid.keys())
                self.print_command(f"ROBOT> Here is what I see: " +
                                   state_tracker_static_obj.get_visual_summary())
                continue

            if intent==UserAct.TASK:
                success, final_goal_conditions_satisfied, final_goal_conditions_total, total_steps = self.runner.eval()
                self.print_command(f"ROBOT> Your task is: {self.runner.check_task.task_name} {self.runner.check_task.task_params}; " +
                                   f"Success: {success}; " +
                                   f"final_goal_conditions_satisfied/final_goal_conditions_total: {final_goal_conditions_satisfied}/{final_goal_conditions_total}")                    
                continue


            if intent==UserAct.CURRENT_PLAN:
                #obj_of_interest = list(state_tracker_static_obj.objects_of_interest_byllmid.keys())
                self.print_command(f'ROBOT> Current plan: {self.runner.planner.get_summary(showstatus=True)}')
                continue

            if intent==UserAct.HISTORY:
                #obj_of_interest = list(state_tracker_static_obj.objects_of_interest_byllmid.keys())
                self.print_command(f'ROBOT> Last state: {self.runner.planner.get_last_dialog_state()}')
                continue 


            if intent==UserAct.SKIP:
                #obj_of_interest = list(state_tracker_static_obj.objects_of_interest_byllmid.keys())
                self.dump_correction(utt)
                self.print_command(f'ROBOT> Marked the last user command as SKIP for annotations')
                continue                        

            if intent==UserAct.COMMAND:   # this is a structured command, do not call llm 
                self.dump_correction(utt)
                self.runner.planner.reset()
                self.runner.planner.set_new_plan(utt, confirmed=True)
                #{ 'utt': 'command', 'context': allcontext, 
                #                           'plan': self.runner.planner.get_summary(showstatus=True, returnjson=True)})    
                continue

            if intent==UserAct.UNKNOWN: # this is not structured command, call LLM to process and convert it into command
                if args.user_type=='direct': # do not call LLM for direct user
                    self.print_command('ROBOT> Invalid command. In direct mode only proper commands are accepted')
                else:

                    self.print_command('ROBOT> Consulting LLM...')
                    self.utt_index +=1

                    # if replan with a new user utterance was successful, show new plan and conrirm
                    # this is the 

                    old_plan = self.runner.planner.get_summary(showstatus=True, returnjson=True)

                    allcontext = state_tracker_static_obj.get_context_for_llm() # TODO: change to version parameter
                    allcontext['old_plan'] = old_plan

                    
                    success, err_msg = self.runner.planner.replan_userutt(utt=utt, context=allcontext)#, old_plan=old_plan)

                    if success is True:

                        new_plan = self.runner.planner.get_summary(showstatus=False, returnjson=True)
                    
                        self.dump_state(utt, old_plan, new_plan, llm_message=self.runner.planner.get_last_llm_message())
                    
                        self.print_command(f'ROBOT> LLM says: \"{self.runner.planner.get_last_llm_message()}\"')

                    else: self.print_command(f'ROBOT> {err_msg}') # TODO- social LLM?

                    # increment only for the utterances processed by LLM
                
                continue
            

            else: self.print_command('ROBOT> I have no idea what you said... ') # TODO- social LLM?
                



        # exited the  while loop (command exit) 
        # if we are running with automatic commander, check success and write output to the file
        success, final_goal_conditions_satisfied, final_goal_conditions_total, total_steps = self.runner.eval()

        result = {'task':self.runner.check_task.task_name, 'param': self.runner.check_task.task_params,'game_id':self.game_id,'success':str(success), 
                      'final_goal_conditions_satisfied': final_goal_conditions_satisfied,
                      'final_goal_conditions_total': final_goal_conditions_total,
                      'steps_num':str(total_steps),
                      'failures':self.runner.planner.failures, 'errors': self.error_messages_log, 'Exception': "; ".join(self.exceptions_log)}
            
        out_fname = f"{args.teach_examples_output + '_' + self.game_start_time + '_result.json'}"
        print(f"writing output to {out_fname}: {json.dumps(result)}")
        with open(out_fname, 'w') as f:
            json.dump(result,f)


        return

if __name__ == "__main__":

    #ir = InteractiveRunner('/rmt/dialogue2/youmna/teach-dataset/', '5ca4283106ee6388_d806', 'valid_unseen') 
    ir = InteractiveRunner('/rmt/dialogue2/youmna/teach-dataset/', args.use_environment, args.use_environment_dsplit) 
    # todo - parameterize if using fullplan or interactive

    if args.planning_mode=='interactive':
        ir.run_interactive() 
    else: print ('Planning mode should be interactive or instate')
    
