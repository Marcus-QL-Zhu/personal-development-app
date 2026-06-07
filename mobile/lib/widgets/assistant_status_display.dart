import 'dart:async';
import 'dart:math';
import 'package:flutter/material.dart';

class AssistantStatusDisplay extends StatefulWidget {
  final String assistantName;
  final bool isThinking;
  final bool isSpeaking;
  final String? statusText;
  final bool enableIdleTimer;

  const AssistantStatusDisplay({
    super.key,
    required this.assistantName,
    this.isThinking = false,
    this.isSpeaking = false,
    this.statusText,
    this.enableIdleTimer = true,
  });

  @override
  State<AssistantStatusDisplay> createState() => _AssistantStatusDisplayState();
}

class _AssistantStatusDisplayState extends State<AssistantStatusDisplay> {
  static const _idleStates = [
    '正在发呆',
    '正在偷吃零食',
    '正在刷手机',
    '正在走神',
    '正在喝茶',
    '正在观察其他玩家表情',
    '正在想晚饭吃什么',
    '正在打哈欠',
    '正在伸懒腰',
    '正在挠痒',
    '正在看窗外',
    '正在喝可乐',
    '正在嚼口香糖',
    '正在摸下巴',
    '正在皱眉思考人生',
    '正在转笔',
    '正在抖腿',
    '正在揉眼睛',
    '正在偷瞄手机通知',
    '正在咬指甲',
    '正在揉太阳穴',
    '正在摸耳朵',
    '正在抖脚',
    '正在整理头发',
    '正在看天花板',
    '正在数手指',
    '正在捏脸',
    '正在深呼吸',
    '正在盯着桌面发呆',
    '正在偷偷打盹',
    '正在补妆',
    '正在照镜子',
    '正在整理刘海',
    '正在涂口红',
    '正在修指甲',
    '正在调整耳环',
    '正在抿嘴唇试色',
    '正在用手遮阳看窗外',
    '正在撩头发',
    '正在整理衣领',
    '正在照小镜子检查妆容',
    '正在抿一口奶茶',
    '正在用手托腮发呆',
    '正在转脖子活动筋骨',
    '正在揉手腕',
    '正在伸懒腰拉伸',
    '正在拍脸颊提神',
    '正在抿一口咖啡',
    '正在闻香水味',
    '正在整理项链',
  ];

  String _currentIdleState = '';
  Timer? _idleTimer;
  final _random = Random();

  @override
  void initState() {
    super.initState();
    _pickIdleState();
    if (widget.enableIdleTimer) {
      _idleTimer =
          Timer.periodic(const Duration(seconds: 30), (_) => _pickIdleState());
    }
  }

  @override
  void dispose() {
    _idleTimer?.cancel();
    super.dispose();
  }

  void _pickIdleState() {
    setState(() {
      _currentIdleState = _idleStates[_random.nextInt(_idleStates.length)];
    });
  }

  String get _displayText {
    if (widget.statusText != null && widget.statusText!.isNotEmpty) {
      return widget.statusText!;
    }
    if (widget.isSpeaking) {
      return '${widget.assistantName}正在说话';
    }
    if (widget.isThinking) {
      return '${widget.assistantName}正在思考';
    }
    return '${widget.assistantName}$_currentIdleState';
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        _displayText,
        style: Theme.of(context).textTheme.bodyMedium,
      ),
    );
  }
}
