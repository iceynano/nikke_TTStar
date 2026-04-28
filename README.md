# How to use

**ONLY PC**

***I just wanted to be lazy, so I simply copied the instructions directly from my previous project.***

Fisrt check your settings page, it should looks like below: (just screen mode and graphic ratio, turn off BGA and set speed to 3.0)

By the way, my desktop resolution is 1920x1080, so if you could just use the resolution👈

~~now script support other resolution,~~ but **1080p** and settings below is still better.

![settings](https://github.com/iceynano/nikke_TTStar/blob/main/settings.png)

**NOTICE: DO NOT MODIFY GAME RESOLUTION WHEN CODE RUNNING**

## Steps

1. edit `config.py` and your game key mapping; in default `config.py`, key mapping from left to right is `a, d, ;, '`. if you are HMT sv player, set `PROCESS_NAME` in `config.py` to `nikke_launcher_hmt.exe`.

2. if you want to install manually, just running `pip install -r requirements.txt`. (if you use venv or other virtual env, I bet you are good at this)  

    if you use release package, just unzip it then double click `start script.bat`, it would auto install requirements to folder under script.

3. run the py file `python main.py` **AS ADMIN** if you install manually, I guess it would be fine if you install requirements properly.

4. go to the minigame area and wait for game start.

   **keep game running foreground and window no obstructed if you want it works**
    
5. you could just keep code running when in game, it would not only press button when rhythm game running (may cause high cpu cost!), so remember quit script if you do not use it.

6. Yes, it can't AP in hard and expert now.

7. If you find code runs badly, ~~I have left extra funcs in script, fork and modify code as you like,~~ try use AI to fix it (what I am doing), I rarely read issues.

8. USE AT YOUR OWN RISK, if you got banned, I would be glad to advise you play games that don't constrain the use of scripts. 

## Debug

there is a toggle(save_buff) in the code to save runtime screenshots for long note detection testing purposes; and you can run `python test.py --image image_path` to check it. for all time saving, you can use `--benchmark` flag like `python main.py --benchmark`. (it may save 7000+ pics per one song!)

## Demo (beta ver)

https://github.com/user-attachments/assets/30e6f4bb-6756-4ff1-abc9-e687d0a2410d