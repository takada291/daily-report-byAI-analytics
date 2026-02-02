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
st.title("🌲 AI日報 解析ダッシュボード")
st.markdown("現場で記録したCSVファイルをアップロードしてください。自動で作業内容を解析します。")

# -------------------------------------------
# 1. ファイルアップロード
# -------------------------------------------
uploaded_file = st.file_uploader("CSVファイルをここにドラッグ＆ドロップ", type="csv")

if uploaded_file is not None:
    try:
        # CSV読み込み
        df = pd.read_csv(uploaded_file)
        
        # 列名チェックと修正（日本語ヘッダー対応）
        rename_map = {'日時': 'time', '緯度': 'lat', '経度': 'lon'}
        df = df.rename(columns=rename_map)
        
        # 必須カラムチェック
        if not {'time', 'lat', 'lon'}.issubset(df.columns):
            st.error("エラー: CSVに必要な列（time, lat, lon）がありません。")
            st.stop()

        # 時間変換
        df['time'] = pd.to_datetime(df['time'])
        df = df.sort_values('time')
        
        # -------------------------------------------
        # 2. 解析ロジック（バックエンド）
        # -------------------------------------------
        
        # 2点間の距離計算関数
        def calc_distance(lat1, lon1, lat2, lon2):
            R = 6371000
            dlat = np.radians(lat2 - lat1)
            dlon = np.radians(lon2 - lon1)
            a = np.sin(dlat/2)**2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon/2)**2
            c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
            return R * c

        # 計算実行
        df['dist_m'] = calc_distance(df['lat'].shift(), df['lon'].shift(), df['lat'], df['lon'])
        df['time_diff'] = df['time'].diff().dt.total_seconds()
        
        # 欠損値埋め
        df = df.fillna(0)
        
        # 速度計算 (km/h)
        # ゼロ除算回避 + データ飛び対策（間隔が長すぎる場合は速度0とする）
        df['speed_kmh'] = np.where((df['time_diff'] > 0) & (df['time_diff'] < 600), 
                                   (df['dist_m'] / df['time_diff']) * 3.6, 0)

        # ステータス判定（時速1.5km以下は滞在）
        threshold = 1.5
        df['status'] = df['speed_kmh'].apply(lambda x: '滞在' if x < threshold else '移動')

        # -------------------------------------------
        # 3. 集計処理
        # -------------------------------------------
        # 変化点だけ抽出して期間計算
        df['group_id'] = (df['status'] != df['status'].shift()).cumsum()
        
        summary = df.groupby(['group_id', 'status']).agg(
            start_time=('time', 'first'),
            end_time=('time', 'last'),
            duration_sec=('time_diff', 'sum')
        ).reset_index()
        
        summary['duration_min'] = summary['duration_sec'] / 60
        summary = summary[summary['duration_min'] > 1] # 1分未満のノイズは無視

        # 総計データ
        total_time = df['time_diff'].sum() / 60
        total_dist = df['dist_m'].sum() / 1000
        stay_time = summary[summary['status']=='滞在']['duration_min'].sum()
        move_time = summary[summary['status']=='移動']['duration_min'].sum()

        # -------------------------------------------
        # 4. 画面表示（ダッシュボード）
        # -------------------------------------------
        
        # KPIカード表示
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("稼働時間", f"{int(total_time)}分")
        col2.metric("移動距離", f"{total_dist:.1f}km")
        col3.metric("作業(滞在)時間", f"{int(stay_time)}分")
        col4.metric("移動時間", f"{int(move_time)}分")

        st.divider()

        # グラフと地図の2カラムレイアウト
        row1_col1, row1_col2 = st.columns([1, 1])

        with row1_col1:
            st.subheader("📊 行動分析")
            
            # 円グラフ (Plotly)
            if stay_time + move_time > 0:
                df_pie = pd.DataFrame({
                    'status': ['作業(滞在)', '移動'],
                    'minutes': [stay_time, move_time]
                })
                fig_pie = px.pie(df_pie, values='minutes', names='status', 
                                 title='作業時間の割合',
                                 color='status',
                                 color_discrete_map={'作業(滞在)':'#ef5350', '移動':'#42a5f5'})
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.write("データ不足のためグラフ表示できません")

            # タイムライン (Plotly Gantt)
            st.write("▼ タイムライン詳細")
            if len(summary) > 0:
                fig_timeline = px.timeline(summary, x_start="start_time", x_end="end_time", 
                                           y="status", color="status",
                                           color_discrete_map={'滞在':'#ef5350', '移動':'#42a5f5'},
                                           hover_data=["duration_min"])
                fig_timeline.update_yaxes(autorange="reversed") # 上から順に
                st.plotly_chart(fig_timeline, use_container_width=True)

        with row1_col2:
            st.subheader("🗺️ 軌跡マップ")
            
            # 地図の中心を計算
            center_lat = df['lat'].mean()
            center_lon = df['lon'].mean()
            
            m = folium.Map(location=[center_lat, center_lon], zoom_start=14)
            
            # 軌跡を描画
            coords = df[['lat', 'lon']].values.tolist()
            if len(coords) > 0:
                folium.PolyLine(coords, color="blue", weight=4, opacity=0.7).add_to(m)
                
                # 開始地点
                folium.Marker(coords[0], popup="開始", icon=folium.Icon(color='green', icon='play')).add_to(m)
                # 終了地点
                folium.Marker(coords[-1], popup="終了", icon=folium.Icon(color='red', icon='stop')).add_to(m)
                
                # 滞在ポイント（作業場所）にマーカー
                stay_points = summary[summary['status'] == '滞在']
                for _, row in stay_points.iterrows():
                    mid_time = row['start_time'] + (row['end_time'] - row['start_time']) / 2
                    nearest_row = df.iloc[(df['time'] - mid_time).abs().argsort()[:1]]
                    lat = nearest_row['lat'].values[0]
                    lon = nearest_row['lon'].values[0]
                    
                    folium.CircleMarker(
                        location=[lat, lon],
                        radius=5,
                        color='red',
                        fill=True,
                        popup=f"作業: {int(row['duration_min'])}分<br>{row['start_time'].strftime('%H:%M')}~"
                    ).add_to(m)

            st_folium(m, width=None, height=500)

    except Exception as e:
        st.error(f"解析中にエラーが発生しました: {e}")
