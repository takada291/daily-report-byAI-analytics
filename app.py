import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import folium
from streamlit_folium import st_folium

# -------------------------------------------
# ページ設定
# -------------------------------------------
st.set_page_config(page_title="AI日報解析クラウド", layout="wide")
st.title("🌲 AI日報 解析ダッシュボード v2.2")
st.markdown("アップロードしたGPSログから速度を算出し、「手作業」「重機」「車両」の3パターンで作業時間を自動解析します。")

# -------------------------------------------
# サイドバー設定（閾値スライダー）
# -------------------------------------------
st.sidebar.header("⚙️ 解析設定")
st.sidebar.markdown("作業内容を判定する「速度の境界線」を調整できます。")

# デフォルト値
default_hand_limit = 1.5
default_crawler_limit = 15.0

# スライダーの設置
hand_threshold = st.sidebar.slider(
    "手作業の上限速度 (km/h)",
    min_value=0.5, max_value=5.0, value=default_hand_limit, step=0.1,
    help="これより遅い動きを「手作業（滞在）」とみなします。"
)

crawler_threshold = st.sidebar.slider(
    "重機移動の上限速度 (km/h)",
    min_value=5.0, max_value=30.0, value=default_crawler_limit, step=1.0,
    help="これより遅い動きを「重機（クローラ）」、速い動きを「車両（ホイール）」とみなします。"
)

st.sidebar.info(f"""
**現在の設定:**
- 🟢 **手作業:** 0 ~ {hand_threshold} km/h
- 🟠 **重機:** {hand_threshold} ~ {crawler_threshold} km/h
- 🔴 **車両:** {crawler_threshold} km/h ~
""")

# -------------------------------------------
# 1. ファイルアップロード
# -------------------------------------------
uploaded_file = st.file_uploader("CSVファイルをここにドラッグ＆ドロップ", type="csv")

if uploaded_file is not None:
    try:
        # CSV読み込み
        df = pd.read_csv(uploaded_file)
        
        # 列名修正
        rename_map = {'日時': 'time', '緯度': 'lat', '経度': 'lon'}
        df = df.rename(columns=rename_map)
        
        if not {'time', 'lat', 'lon'}.issubset(df.columns):
            st.error("エラー: CSVに必要な列（time, lat, lon）がありません。")
            st.stop()

        # 時間変換・ソート
        df['time'] = pd.to_datetime(df['time'])
        df = df.sort_values('time')
        
        # -------------------------------------------
        # 2. 解析ロジック（可変閾値対応）
        # -------------------------------------------
        
        def calc_distance(lat1, lon1, lat2, lon2):
            R = 6371000
            dlat = np.radians(lat2 - lat1)
            dlon = np.radians(lon2 - lon1)
            a = np.sin(dlat/2)**2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon/2)**2
            c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
            return R * c

        df['dist_m'] = calc_distance(df['lat'].shift(), df['lon'].shift(), df['lat'], df['lon'])
        df['time_diff'] = df['time'].diff().dt.total_seconds()
        df = df.fillna(0)
        
        # 速度計算 (データ飛び対策: 600秒以上の空白は速度0扱い)
        df['speed_kmh'] = np.where((df['time_diff'] > 0) & (df['time_diff'] < 600), 
                                   (df['dist_m'] / df['time_diff']) * 3.6, 0)

        # 3パターン判定（スライダーの値を使用）
        def classify_status(speed):
            if speed < hand_threshold:
                return '手作業(滞在)'   # 緑
            elif speed < crawler_threshold:
                return '重機(クローラ)' # 橙
            else:
                return '車両(ホイール)' # 赤
        
        df['status'] = df['speed_kmh'].apply(classify_status)

        # -------------------------------------------
        # 3. 集計処理
        # -------------------------------------------
        df['group_id'] = (df['status'] != df['status'].shift()).cumsum()
        
        summary = df.groupby(['group_id', 'status']).agg(
            start_time=('time', 'first'),
            end_time=('time', 'last'),
            duration_sec=('time_diff', 'sum')
        ).reset_index()
        
        summary['duration_min'] = summary['duration_sec'] / 60
        summary = summary[summary['duration_min'] > 1] # 1分未満は無視

        # KPI集計
        total_time = df['time_diff'].sum() / 60
        total_dist = df['dist_m'].sum() / 1000
        
        time_hand = summary[summary['status']=='手作業(滞在)']['duration_min'].sum()
        time_crawler = summary[summary['status']=='重機(クローラ)']['duration_min'].sum()
        time_wheel = summary[summary['status']=='車両(ホイール)']['duration_min'].sum()

        # -------------------------------------------
        # 4. 画面表示
        # -------------------------------------------
        
        # 色と並び順の定義
        color_map = {
            '手作業(滞在)': '#66bb6a',   # 緑
            '重機(クローラ)': '#ffa726', # オレンジ
            '車両(ホイール)': '#ef5350'  # 赤
        }
        order_list = ['手作業(滞在)', '重機(クローラ)', '車両(ホイール)']

        # KPIカード
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🌲 手作業(滞在)", f"{int(time_hand)}分")
        c2.metric("🚜 重機(クローラ)", f"{int(time_crawler)}分")
        c3.metric("🚚 車両(ホイール)", f"{int(time_wheel)}分")
        c4.metric("総移動距離", f"{total_dist:.1f}km")

        st.divider()

        # レイアウト
        row1_col1, row1_col2 = st.columns([1, 1])

        with row1_col1:
            st.subheader("📊 作業バランス")
            
            # 円グラフ
            if total_time > 0:
                df_pie = pd.DataFrame({
                    'status': ['手作業(滞在)', '重機(クローラ)', '車両(ホイール)'],
                    'minutes': [time_hand, time_crawler, time_wheel]
                })
                # 0分の項目は消す
                df_pie = df_pie[df_pie['minutes'] > 0]
                
                # 並び順を指定してソート
                df_pie['status'] = pd.Categorical(df_pie['status'], categories=order_list, ordered=True)
                df_pie = df_pie.sort_values('status')

                fig_pie = px.pie(df_pie, values='minutes', names='status', 
                                 title='作業時間の割合',
                                 color='status',
                                 color_discrete_map=color_map,
                                 category_orders={'status': order_list})
                st.plotly_chart(fig_pie, use_container_width=True)
            
            # タイムライン
            st.write("▼ タイムライン")
            if len(summary) > 0:
                fig_timeline = px.timeline(summary, x_start="start_time", x_end="end_time", 
                                           y="status", color="status",
                                           color_discrete_map=color_map,
                                           hover_data=["duration_min"],
                                           category_orders={'status': order_list})
                
                fig_timeline.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_timeline, use_container_width=True)

        with row1_col2:
            st.subheader("🗺️ 現場マップ")
            
            center_lat = df['lat'].mean()
            center_lon = df['lon'].mean()
            m = folium.Map(location=[center_lat, center_lon], zoom_start=14)
            
            # 軌跡
            coords = df[['lat', 'lon']].values.tolist()
            if len(coords) > 0:
                folium.PolyLine(coords, color="blue", weight=3, opacity=0.5).add_to(m)
                
                # 「手作業」の場所だけ緑の点を打つ（スライダーで変化する手作業範囲に対応）
                hand_df = df[df['status'] == '手作業(滞在)']
                # 点が多すぎる場合の軽量化（データ数に応じて間引き）
                step = max(1, len(hand_df) // 100) 
                
                for _, row in hand_df.iloc[::step].iterrows():
                    folium.CircleMarker(
                        location=[row['lat'], row['lon']],
                        radius=3,
                        color='#66bb6a',
                        fill=True,
                        fill_opacity=0.7,
                        popup=f"{row['time'].strftime('%H:%M')}"
                    ).add_to(m)
                
                folium.Marker(coords[0], popup="開始", icon=folium.Icon(color='green', icon='play')).add_to(m)
                folium.Marker(coords[-1], popup="終了", icon=folium.Icon(color='red', icon='stop')).add_to(m)

            st_folium(m, width=None, height=500)

    except Exception as e:
        st.error(f"エラーが発生しました: {e}")
